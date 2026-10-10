import {
  forwardRef,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  ArrowUp,
  CircleAlert,
  CircleStop,
  CornerDownRight,
  FileText,
  ListTree,
  LoaderCircle,
  MessageSquareText,
  RotateCcw,
  Search,
  Sparkles,
  TriangleAlert,
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
import { newRequestId, pending, type LiveAnswer } from './answers';
import {
  useActions,
  useConfigured,
  useLiveAnswer,
  usePreferences,
  useSearch,
  useStores,
} from './state';
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
  // The question this session waits for the answer to ('' while none) and its request. The answer's text comes into
  // the answers slice as the LLM writes it (useLiveAnswer); it goes on while the session shows another conversation,
  // and shows again when the session comes back to its conversation.
  asking: string;
  requestId: string | null;
  askError: string;
  // The question that failed, or that the backend refused while its conversation still answered another, to send again.
  failed: string;
  // Follow-up questions continue the conversation until the chip in the search box is removed.
  followUp: boolean;
  // A question about one document answers from it alone, without searching.
  scope: Scope | null;
};
type Scope = { id: string; title: string };

// The session showing a conversation: its saved messages and the answer the answers slice holds for it, still being
// written (shown as it comes) or saved since the messages were read (then its newer messages).
function conversationSession(
  conversationId: string | null,
  title: string,
  messages: ChatMessage[],
  live: LiveAnswer | null,
): Session {
  const saved = live?.result && live.result.messages.length >= messages.length ? live.result : null;
  const waiting = live && pending(live) ? live : null;
  return {
    query: title,
    local: null,
    searching: false,
    localError: '',
    conversationId: saved?.conversation_id ?? conversationId,
    messages: saved?.messages ?? messages,
    ai: saved?.search ?? null,
    asking: waiting?.question ?? '',
    requestId: waiting?.requestId ?? null,
    askError: '',
    failed: '',
    followUp: true,
    scope: null,
  };
}

// A question in a message, cut to a line.
const clip = (text: string) => (text.length > 40 ? `${text.slice(0, 40)}…` : text);

// One search box for everything: a local search answers at once, and with the LLM connected the
// assistant searches with AI, answers from the documents it picked, and keeps the conversation.
// The answer streams: its text shows as the LLM writes it, and 중지 stops it, keeping what came.
export function useSearchSession({
  configured,
  aiAnswer,
}: {
  configured: boolean;
  aiAnswer: boolean;
}) {
  const { answers, actions } = useStores();
  const { notify, undoable } = actions;
  const [state, setState] = useState<Session | null>(null);
  // Every conversation saved or deleted; lists of recent conversations reload on changes.
  const [saved, setSaved] = useState(0);
  const [scope, setScope] = useState<Scope | null>(null);
  // Counts the searches and conversations shown, so that a local search or a history that comes late is dropped.
  const generation = useRef(0);
  // The session as last set, ahead of React drawing it: an answer that comes decides by it whether it is shown.
  const latest = useRef<Session | null>(null);
  const update = useCallback(
    (next: Session | null | ((current: Session | null) => Session | null)) => {
      const value = typeof next === 'function' ? next(latest.current) : next;
      latest.current = value;
      setState(value);
    },
    [],
  );
  // The parts and ends of every answer this screen asks for.
  useEffect(() => answers.listen(), [answers]);
  const open = useCallback(
    async (conversation: Conversation) => {
      const id = ++generation.current;
      const messages = await api<ChatMessage[]>('assistant.history', { id: conversation.id });
      if (id !== generation.current) return;
      update(
        conversationSession(
          conversation.id,
          conversation.title,
          messages,
          answers.latest(conversation.id),
        ),
      );
    },
    [answers, update],
  );
  // A conversation the lists show from the answers slice before it is saved (a new one): its answer as it comes, or as
  // it was saved a moment ago.
  const follow = useCallback(
    async (requestId: string) => {
      const live = answers.get(requestId);
      if (!live) return;
      const id = ++generation.current;
      const messages = live.conversationId
        ? await api<ChatMessage[]>('assistant.history', { id: live.conversationId })
        : [];
      if (id !== generation.current) return;
      update(
        conversationSession(live.conversationId, live.question, messages, answers.get(requestId)),
      );
    },
    [answers, update],
  );
  const ask = useCallback(
    async (question: string, conversationId: string | null, about: Scope | null) => {
      const requestId = newRequestId();
      answers.start(requestId, question, conversationId);
      update((s) => s && { ...s, asking: question, requestId, askError: '', failed: '' });
      let answer: Answer;
      try {
        answer = await api<Answer>('assistant.ask', {
          question,
          conversation_id: conversationId,
          document_ids: about ? [about.id] : [],
          stream: true,
          request_id: requestId,
        });
      } catch (error) {
        // Refused (the conversation still answers another question) or failed: nothing of it was saved.
        answers.fail(requestId);
        const message = (error as Error).message;
        if (latest.current?.requestId === requestId)
          update(
            (s) => s && { ...s, asking: '', requestId: null, askError: message, failed: question },
          );
        else notify(`‘${clip(question)}’ 질문에 답하지 못했습니다. ${message}`, true);
        return;
      }
      answers.finish(requestId, answer);
      setSaved((value) => value + 1);
      // The saved answer takes the place of the one being written in the same drawing, so it never shows twice.
      if (latest.current?.requestId === requestId)
        update(
          (s) =>
            s && {
              ...s,
              conversationId: answer.conversation_id,
              messages: answer.messages,
              ai: answer.search,
              asking: '',
              requestId: null,
              followUp: true,
            },
        );
      else {
        // The session went to another conversation, or was closed, while the answer was written.
        const title =
          answer.messages.find((message) => message.role === 'user')?.content.slice(0, 100) ??
          question;
        undoable(`AI 답변을 마쳤습니다 · ${clip(question)}`, {
          label: '보기',
          run: () =>
            open({ id: answer.conversation_id, title, updated_at: '' }).catch((e) =>
              notify((e as Error).message, true),
            ),
        });
      }
    },
    [answers, update, open, notify, undoable],
  );
  // `everywhere`: a keyword searches all documents, whatever the next question is scoped to.
  const submit = useCallback(
    (raw: string, followUp: boolean, everywhere = false) => {
      const query = raw.trim();
      const previous = latest.current;
      if (!query || previous?.asking) return;
      const id = ++generation.current;
      const conversationId = followUp && previous?.followUp ? previous.conversationId : null;
      const about = everywhere ? null : scope;
      update({
        query,
        local: null,
        searching: !about,
        localError: '',
        conversationId,
        messages: conversationId ? previous!.messages : [],
        ai: null,
        asking: '',
        requestId: null,
        askError: '',
        failed: '',
        followUp: !!conversationId,
        scope: about,
      });
      if (about) {
        if (configured) void ask(query, conversationId, about);
        return;
      }
      api<SearchResult>('wiki.search', { query, mode: 'auto' })
        .then((local) => {
          if (id === generation.current) update((s) => s && { ...s, local, searching: false });
        })
        .catch((error) => {
          if (id === generation.current)
            update((s) => s && { ...s, searching: false, localError: error.message });
        });
      // A conversation always continues with the AI; a new search asks when answers are automatic.
      if (configured && (aiAnswer || conversationId)) void ask(query, conversationId, null);
    },
    [ask, configured, aiAnswer, scope, update],
  );
  const askNow = useCallback(() => {
    const s = latest.current;
    if (s && !s.asking) void ask(s.query, s.followUp ? s.conversationId : null, s.scope);
  }, [ask]);
  // The question that failed or was refused, once more, in the conversation the search box continues.
  const resend = useCallback(() => {
    const s = latest.current;
    if (s && !s.asking && s.failed)
      void ask(s.failed, s.followUp ? s.conversationId : null, s.scope);
  }, [ask]);
  // 중지: the answer ends as soon as the backend can end it, and what came of it is saved, marked as stopped.
  const stop = useCallback(() => {
    const requestId = latest.current?.requestId;
    if (requestId)
      void answers.stop(requestId).catch((error) => notify((error as Error).message, true));
  }, [answers, notify]);
  const reset = useCallback(() => {
    generation.current++;
    update(null);
  }, [update]);
  const endConversation = useCallback(
    () => update((s) => s && { ...s, followUp: false }),
    [update],
  );
  const forget = useCallback(
    (conversationId: string) => {
      answers.forget(conversationId);
      setSaved((value) => value + 1);
      if (latest.current?.conversationId === conversationId) {
        generation.current++;
        update(null);
      }
    },
    [answers, update],
  );
  return useMemo(
    () => ({
      state,
      saved,
      scope,
      setScope,
      submit,
      askNow,
      resend,
      stop,
      open,
      follow,
      reset,
      endConversation,
      forget,
    }),
    [
      state,
      saved,
      scope,
      submit,
      askNow,
      resend,
      stop,
      open,
      follow,
      reset,
      endConversation,
      forget,
    ],
  );
}
export type SearchSession = ReturnType<typeof useSearchSession>;

export const Omnibox = forwardRef<HTMLInputElement, { onSubmit: () => void }>(function Omnibox(
  { onSubmit },
  ref,
) {
  const search = useSearch();
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
  const search = useSearch();
  const configured = useConfigured();
  const preferences = usePreferences();
  const { openAdmin, openDocument, searchKeyword } = useActions();
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
  // The question being answered, or the one that failed, as a question of its own, unless the heading shows it already
  // (the first question of a conversation).
  const asked = (question: string) =>
    (state.messages.length > 0 || question !== state.query) && (
      <p className="question">{question}</p>
    );
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
        {/* Busy while an answer is written: a screen reader reads the answer once it is whole, not every part. */}
        <div
          className="answer-column"
          aria-label="AI 답변"
          aria-live="polite"
          aria-busy={state.asking ? true : undefined}
        >
          {state.messages.map((message, index) =>
            // The heading already shows the first question.
            index === 0 && message.role === 'user' && message.content === state.query ? null : (
              <MessageView key={message.seq} message={message} />
            ),
          )}
          {state.asking && (
            <>
              {asked(state.asking)}
              <LiveAnswerView requestId={state.requestId} onStop={search.stop} />
            </>
          )}
          {state.askError && (
            <>
              {state.failed && asked(state.failed)}
              <div className="inline-error ask-error" role="alert">
                <CircleAlert size={14} aria-hidden="true" />
                <span>{state.askError}</span>
                {state.failed && (
                  <button type="button" className="text-button" onClick={search.resend}>
                    <RotateCcw size={13} aria-hidden="true" />
                    다시 보내기
                  </button>
                )}
              </div>
            </>
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

// What an answer that did not end by itself says about it, as the backend words it: the user stopped it, or the LLM's
// output limit cut it short (backend/agent/assistant.py STOPPED, backend/agent/llm.py TRUNCATED_NOTE). The saved answer
// carries the backend's own words; these show while the answer is being saved.
const STOPPED_NOTE = '답변을 중지했습니다.';
const TRUNCATED_NOTE =
  '답변이 LLM의 최대 출력 길이에서 잘렸습니다. 관리 → LLM 연결에서 최대 출력 토큰을 늘리거나 요청을 줄여 다시 시도해 주세요.';
type Outcome = 'cancelled' | 'truncated';

// A saved answer that did not end by itself ends with the backend's note on why, a paragraph of its own in parentheses
// (assistant.py noted()): it is shown apart from the answer, with what the user can do about it.
function splitNote(content: string, outcome: Outcome | undefined): [string, string] {
  if (!outcome || !content.endsWith(')')) return [content, ''];
  const at = content.lastIndexOf('\n\n(');
  if (at >= 0) return [content.slice(0, at).trimEnd(), content.slice(at + 3, -1)];
  return content.startsWith('(') ? ['', content.slice(1, -1)] : [content, ''];
}

function AnswerNote({ outcome, text }: { outcome: Outcome; text: string }) {
  const { openAdmin } = useActions();
  return (
    <p className={`answer-note ${outcome}`}>
      {outcome === 'truncated' ? (
        <TriangleAlert size={14} aria-hidden="true" />
      ) : (
        <CircleStop size={14} aria-hidden="true" />
      )}
      <span>{text}</span>
      {outcome === 'truncated' && (
        <button type="button" className="text-button" onClick={() => openAdmin('llm')}>
          LLM 설정 열기
        </button>
      )}
    </p>
  );
}

// How often an answer being written is drawn again: its parts come every 50ms, and Markdown drawn at each of them would
// jump (a list or a table taking shape) and cost the time of parsing it all again.
const DRAW_MS = 150;

// A code block whose closing fence has not come yet is closed for the drawing, so the rest of the answer does not turn
// into code and back as the fence arrives.
function closeFences(text: string) {
  const fences = text.match(/^ {0,3}(?:```|~~~)/gm)?.length ?? 0;
  return fences % 2 ? `${text}\n\`\`\`` : text;
}

// The text of an answer being written, drawn at most every DRAW_MS while it runs and at once when it ends. The answer
// column follows the end of the text, unless the user scrolled up to read.
function StreamedMarkdown({ text, running }: { text: string; running: boolean }) {
  const [shown, setShown] = useState(text);
  const drawn = useRef(0);
  const box = useRef<HTMLDivElement>(null);
  const following = useRef(false);
  useEffect(() => {
    if (text === shown) return;
    const draw = () => {
      const column = box.current?.closest('.answer-column');
      following.current =
        !!column && column.scrollHeight - column.scrollTop - column.clientHeight < 48;
      drawn.current = performance.now();
      setShown(text);
    };
    const wait = running ? DRAW_MS - (performance.now() - drawn.current) : 0;
    if (wait <= 0) return draw();
    const timer = setTimeout(draw, wait);
    return () => clearTimeout(timer);
  }, [text, running, shown]);
  useLayoutEffect(() => {
    if (!following.current) return;
    const column = box.current?.closest('.answer-column');
    column?.scrollTo({ top: column.scrollHeight });
  }, [shown]);
  return (
    <div ref={box} className="answer-text">
      <Markdown>{closeFences(shown)}</Markdown>
    </div>
  );
}

// The answer while the LLM writes it: its text as it comes, 답변 중지 until it ends, and how it ended (stopped, or cut at
// the output limit) until the saved answer takes its place. Only this part draws again with every part of the answer.
function LiveAnswerView({ requestId, onStop }: { requestId: string | null; onStop: () => void }) {
  const answer = useLiveAnswer(requestId);
  const running = !answer || (pending(answer) && answer.status === 'streaming');
  const stop = (
    <button
      type="button"
      className="button secondary small stop-answer"
      disabled={!answer || answer.stopping}
      onClick={onStop}
    >
      <CircleStop size={14} aria-hidden="true" />
      {answer?.stopping ? '중지하는 중…' : '답변 중지'}
    </button>
  );
  // Before its first words the question is analyzed and the documents searched, which 중지 also ends.
  if (!answer || (running && !answer.text))
    return (
      <div className="thinking">
        <LoaderCircle className="spin" size={16} aria-hidden="true" />
        <span>문서를 찾고 답변을 쓰고 있어요. LLM 응답에 시간이 걸릴 수 있습니다.</span>
        {stop}
      </div>
    );
  return (
    <article className={`answer live${answer.status === 'cancelled' ? ' stopped' : ''}`}>
      <StreamedMarkdown text={answer.text} running={running} />
      {running ? (
        <div className="answer-progress">
          <LoaderCircle className="spin" size={14} aria-hidden="true" />
          <span>답변을 쓰고 있어요</span>
          {stop}
        </div>
      ) : answer.status === 'cancelled' ? (
        <AnswerNote outcome="cancelled" text={STOPPED_NOTE} />
      ) : answer.truncated ? (
        <AnswerNote outcome="truncated" text={TRUNCATED_NOTE} />
      ) : null}
    </article>
  );
}

// A question or an answer with its search steps and the evidence it cites. An answer the user stopped, or one the
// LLM's output limit cut short, says so under what came of it.
function MessageView({ message }: { message: ChatMessage }) {
  const { notify, openDocument } = useActions();
  if (message.role === 'user') return <p className="question">{message.content}</p>;
  const outcome = message.trace?.outcome;
  const [body, note] = splitNote(message.content, outcome);
  return (
    <article className={`answer${outcome === 'cancelled' ? ' stopped' : ''}`}>
      {body && <Markdown>{body}</Markdown>}
      {outcome && (
        <AnswerNote
          outcome={outcome}
          text={note || (outcome === 'cancelled' ? STOPPED_NOTE : TRUNCATED_NOTE)}
        />
      )}
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
