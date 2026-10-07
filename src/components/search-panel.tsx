import { forwardRef, useCallback, useMemo, useRef, useState } from 'react';
import {
  ArrowUp,
  CornerDownRight,
  FileText,
  ListTree,
  LoaderCircle,
  MessageSquareText,
  Search,
  Sparkles,
  X,
} from 'lucide-react';
import {
  api,
  date,
  type Answer,
  type ChatMessage,
  type Conversation,
  type SearchResult,
} from '@/lib/api';
import { useApp } from './app';
import Highlight from './highlight';
import Markdown from './markdown';
import { ResultCard, SearchSteps } from './search-results';

type Session = {
  query: string;
  // The local search's documents for the latest query; null while it runs or for a reopened
  // conversation.
  local: SearchResult | null;
  searching: boolean;
  localError: string;
  conversationId: string | null;
  messages: ChatMessage[];
  // What the AI search found for the latest answer: its picks first, then the other matches.
  ai: SearchResult | null;
  asking: string;
  askError: string;
  // Follow-up questions continue the conversation until the chip in the search box is removed.
  followUp: boolean;
  // A question about one document answers from it alone, without searching.
  scope: Scope | null;
};
type Scope = { id: string; title: string };

// One search box for everything: a local search answers at once, and with the LLM connected the
// assistant searches with AI, answers from the documents it picked, and keeps the conversation.
export function useSearchSession({
  configured,
  aiAnswer,
}: {
  configured: boolean;
  aiAnswer: boolean;
}) {
  const [state, setState] = useState<Session | null>(null);
  // Every conversation saved or deleted; lists of recent conversations reload on changes.
  const [saved, setSaved] = useState(0);
  const [scope, setScope] = useState<Scope | null>(null);
  const generation = useRef(0);
  const current = useRef(state);
  current.current = state;
  const ask = useCallback(
    async (question: string, conversationId: string | null, id: number, about: Scope | null) => {
      setState((s) => s && { ...s, asking: question, askError: '' });
      try {
        const answer = await api<Answer>('assistant.ask', {
          question,
          conversation_id: conversationId,
          document_ids: about ? [about.id] : [],
        });
        if (id !== generation.current) return;
        setState(
          (s) =>
            s && {
              ...s,
              conversationId: answer.conversation_id,
              messages: answer.messages,
              ai: answer.search,
              asking: '',
              followUp: true,
            },
        );
        setSaved((value) => value + 1);
      } catch (error) {
        if (id === generation.current)
          setState((s) => s && { ...s, asking: '', askError: (error as Error).message });
      }
    },
    [],
  );
  // `everywhere`: a keyword searches all documents, whatever the next question is scoped to.
  const submit = useCallback(
    (raw: string, followUp: boolean, everywhere = false) => {
      const query = raw.trim();
      const previous = current.current;
      if (!query || previous?.asking) return;
      const id = ++generation.current;
      const conversationId = followUp && previous?.followUp ? previous.conversationId : null;
      const about = everywhere ? null : scope;
      setState({
        query,
        local: null,
        searching: !about,
        localError: '',
        conversationId,
        messages: conversationId ? previous!.messages : [],
        ai: null,
        asking: '',
        askError: '',
        followUp: !!conversationId,
        scope: about,
      });
      if (about) {
        if (configured) void ask(query, conversationId, id, about);
        return;
      }
      api<SearchResult>('wiki.search', { query, mode: 'auto' })
        .then((local) => {
          if (id === generation.current) setState((s) => s && { ...s, local, searching: false });
        })
        .catch((error) => {
          if (id === generation.current)
            setState((s) => s && { ...s, searching: false, localError: error.message });
        });
      // A conversation always continues with the AI; a new search asks when answers are automatic.
      if (configured && (aiAnswer || conversationId)) void ask(query, conversationId, id, null);
    },
    [ask, configured, aiAnswer, scope],
  );
  const askNow = useCallback(() => {
    const s = current.current;
    if (s && !s.asking)
      void ask(s.query, s.followUp ? s.conversationId : null, generation.current, s.scope);
  }, [ask]);
  const open = useCallback(async (conversation: Conversation) => {
    const id = ++generation.current;
    const messages = await api<ChatMessage[]>('assistant.history', { id: conversation.id });
    if (id !== generation.current) return;
    setState({
      query: conversation.title,
      local: null,
      searching: false,
      localError: '',
      conversationId: conversation.id,
      messages,
      ai: null,
      asking: '',
      askError: '',
      followUp: true,
      scope: null,
    });
  }, []);
  const reset = useCallback(() => {
    generation.current++;
    setState(null);
  }, []);
  const endConversation = useCallback(() => setState((s) => s && { ...s, followUp: false }), []);
  const forget = useCallback((conversationId: string) => {
    setSaved((value) => value + 1);
    if (current.current?.conversationId === conversationId) {
      generation.current++;
      setState(null);
    }
  }, []);
  return useMemo(
    () => ({ state, saved, scope, setScope, submit, askNow, open, reset, endConversation, forget }),
    [state, saved, scope, submit, askNow, open, reset, endConversation, forget],
  );
}
export type SearchSession = ReturnType<typeof useSearchSession>;

export const Omnibox = forwardRef<HTMLInputElement, { onSubmit: () => void }>(function Omnibox(
  { onSubmit },
  ref,
) {
  const { search } = useApp();
  const [value, setValue] = useState('');
  const state = search.state;
  const continuing = !!state?.followUp && !!state.conversationId;
  return (
    <form
      className="omnibox"
      role="search"
      onSubmit={(event) => {
        event.preventDefault();
        if (!value.trim() || state?.asking) return;
        onSubmit();
        search.submit(value, true);
        setValue('');
      }}
    >
      <Search size={18} aria-hidden="true" />
      {search.scope && (
        <button
          type="button"
          className="omnibox-chip scope"
          title={`‘${search.scope.title}’ 문서에만 질문합니다. 누르면 전체 검색으로 돌아갑니다.`}
          aria-label={`${search.scope.title} 문서에 질문하기 끝내기`}
          onClick={() => search.setScope(null)}
        >
          <FileText size={13} />
          <span className="chip-text">{search.scope.title}</span>
          <X size={13} />
        </button>
      )}
      {continuing && (
        <button
          type="button"
          className="omnibox-chip"
          title="새 질문으로 시작하려면 누르세요"
          aria-label="대화 이어가기 끝내기"
          onClick={search.endConversation}
        >
          <MessageSquareText size={13} />
          이어서 질문
          <X size={13} />
        </button>
      )}
      <input
        ref={ref}
        aria-label="검색 또는 질문"
        value={value}
        maxLength={2000}
        placeholder={
          search.scope
            ? '이 문서에 대해 물어보세요'
            : continuing
              ? '이어서 물어보세요'
              : '문서 검색 또는 AI에게 질문하기'
        }
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && event.nativeEvent.isComposing) event.preventDefault();
          if (event.key !== 'Escape') return;
          if (value) setValue('');
          else search.reset();
        }}
      />
      {state?.asking ? (
        <LoaderCircle className="spin" size={17} aria-label="답변을 기다리는 중" />
      ) : (
        <kbd>Ctrl K</kbd>
      )}
      <button
        type="submit"
        className="omnibox-go"
        aria-label="검색"
        disabled={!value.trim() || !!state?.asking}
      >
        <ArrowUp size={17} />
      </button>
    </form>
  );
});

export default function SearchPanel() {
  const { search, configured, preferences, openAdmin, openDocument, searchKeyword } = useApp();
  const state = search.state!;
  const terms = useMemo(
    () => [...new Set([...(state.local?.highlights ?? []), ...(state.ai?.highlights ?? [])])],
    [state.local, state.ai],
  );
  const picked = state.ai?.documents.filter((doc) => doc.match.selected) ?? [];
  const others = useMemo(() => {
    const seen = new Set(picked.map((doc) => doc.id));
    return [...(state.local?.documents ?? []), ...(state.ai?.documents ?? [])].filter((doc) => {
      if (seen.has(doc.id)) return false;
      seen.add(doc.id);
      return true;
    });
  }, [state.local, state.ai, picked]);
  const card = (doc: SearchResult['documents'][number]) => (
    <ResultCard
      key={doc.id}
      doc={doc}
      terms={terms}
      busy={false}
      onOpen={() =>
        openDocument(doc.id, { terms, sections: doc.match.sections.map((section) => section.id) })
      }
      onKeyword={searchKeyword}
    />
  );
  const found = picked.length + others.length;
  return (
    <section className="search-panel" aria-label="검색 결과">
      <header className="search-panel-head">
        <div>
          <h2>{state.query}</h2>
          <p>
            {state.searching
              ? '찾는 중…'
              : state.local || state.ai
                ? `문서 ${found}개${state.local && state.local.total > state.local.documents.length ? ` · 전체 ${state.local.total}개 중 관련도 상위` : ''}`
                : '저장된 대화'}
          </p>
        </div>
        <button className="icon-button" aria-label="검색 결과 닫기" onClick={search.reset}>
          <X size={18} />
        </button>
      </header>
      <div className="search-columns">
        <div className="answer-column" aria-label="AI 답변" aria-live="polite">
          {state.messages.map((message, index) =>
            // The heading already shows the first question.
            index === 0 && message.role === 'user' && message.content === state.query ? null : (
              <MessageView key={message.seq} message={message} />
            ),
          )}
          {state.asking && (
            <>
              {state.conversationId && <p className="question">{state.asking}</p>}
              <div className="thinking">
                <LoaderCircle className="spin" size={16} />
                문서를 찾고 답변을 쓰고 있어요. LLM 응답에 시간이 걸릴 수 있습니다.
              </div>
            </>
          )}
          {state.askError && (
            <p className="inline-error" role="alert">
              {state.askError}
            </p>
          )}
          {!state.messages.length && !state.asking && !state.askError && (
            <div className="answer-empty">
              {!configured ? (
                <>
                  <p>LLM을 연결하면 AI가 관련 문서를 골라 근거와 함께 답변합니다.</p>
                  <button className="button secondary small" onClick={() => openAdmin('llm')}>
                    LLM 연결하기
                  </button>
                </>
              ) : !preferences.ai_answer ? (
                <>
                  <p>AI 답변을 자동으로 만들지 않도록 설정되어 있습니다.</p>
                  <button className="button secondary small" onClick={search.askNow}>
                    <Sparkles size={14} />
                    AI 답변 받기
                  </button>
                </>
              ) : null}
            </div>
          )}
        </div>
        <div className="results-column" aria-label="찾은 문서">
          {state.scope && (
            <>
              <h3 className="result-group">
                <FileText size={14} />
                질문한 문서
              </h3>
              <button className="scope-card" onClick={() => openDocument(state.scope!.id)}>
                <strong>{state.scope.title}</strong>
                <small>
                  이 문서만 읽고 답합니다. 검색창의 문서 표시를 지우면 전체에서 찾습니다.
                </small>
              </button>
            </>
          )}
          {state.localError && (
            <p className="inline-error" role="alert">
              {state.localError}
            </p>
          )}
          {picked.length > 0 && (
            <>
              <h3 className="result-group">
                <Sparkles size={14} />
                AI가 고른 문서 {picked.length}개
              </h3>
              {picked.map(card)}
            </>
          )}
          {state.ai && !state.ai.ai && (
            <p className="result-note">
              AI 검색을 사용하지 못해 단어가 일치한 문서를 보여 드립니다.
            </p>
          )}
          {others.length > 0 && (
            <>
              <h3 className="result-group">
                {picked.length
                  ? `일치하는 다른 문서 ${others.length}개`
                  : `일치하는 문서 ${others.length}개`}
              </h3>
              {others.map(card)}
            </>
          )}
          {!found && !state.searching && !state.localError && state.local && (
            <p className="empty-note">
              일치하는 문서가 없습니다. 다른 단어로 찾거나 자료를 가져와 보세요.
            </p>
          )}
          {state.searching && (
            <div className="thinking">
              <LoaderCircle className="spin" size={16} />
              문서를 찾는 중…
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

// A question or an answer with its search steps and the evidence it cites.
function MessageView({ message }: { message: ChatMessage }) {
  const { notify, openDocument } = useApp();
  if (message.role === 'user') return <p className="question">{message.content}</p>;
  return (
    <article className="answer">
      <Markdown>{message.content}</Markdown>
      {message.citations.length > 0 && (
        <div className="citations">
          {message.citations.map((c) => (
            <details key={c.number}>
              <summary>
                [{c.number}] {c.title}
              </summary>
              <small>
                답변에 사용한 문서 · {date(c.updated_at)} 저장{c.partial ? ' · 일부 발췌' : ''}
              </small>
              {!!c.sections?.length && (
                <span className="citation-sections">
                  {c.sections.map((section) => (
                    <span key={section}>
                      <ListTree size={11} />
                      {section}
                    </span>
                  ))}
                </span>
              )}
              <blockquote>
                <Highlight text={c.excerpt} terms={c.terms} />
              </blockquote>
              <div className="citation-actions">
                <button
                  className="text-button"
                  onClick={() => openDocument(c.id, { terms: c.terms ?? [], sections: [] })}
                >
                  현재 문서 열기
                </button>
                {/* The source's link or file path, to open it outside the app. */}
                {c.source && (
                  <button
                    className="text-button"
                    onClick={() =>
                      void api('clipboard.write', {
                        text:
                          c.source &&
                          ('source_url' in c.source ? c.source.source_url : c.source.source_path),
                      })
                        .then(() => notify('원본 주소를 복사했습니다.'))
                        .catch((e) => notify(e.message, true))
                    }
                  >
                    원본 주소 복사
                  </button>
                )}
              </div>
            </details>
          ))}
        </div>
      )}
      {message.trace && message.trace.steps.length > 0 && (
        <details className="trace">
          <summary>검색 과정 · {message.trace.steps.map((step) => step.label).join(' → ')}</summary>
          <SearchSteps steps={message.trace.steps} />
          {message.trace.sources.some((source) => source.sections.length) && (
            <p>
              <CornerDownRight size={12} />
              읽은 섹션:{' '}
              {message.trace.sources
                .filter((source) => source.sections.length)
                .map((source) => `${source.title} (${source.sections.join(', ')})`)
                .join(' · ')}
            </p>
          )}
        </details>
      )}
      <small className="answer-time">{date(message.created_at)}</small>
    </article>
  );
}
