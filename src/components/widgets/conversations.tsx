import { useEffect } from 'react';
import { LoaderCircle, MessageSquareText, Trash2 } from 'lucide-react';
import { api, date, type Conversation } from '@/lib/api';
import Confirm from '../confirm';
import { WidgetHead } from '../dashboard';
import { useActions, useLiveConversations, useQuery, useSearch } from '../state';

export default function ConversationsWidget() {
  const search = useSearch();
  const { notify } = useActions();
  // Loaded again whenever a conversation is saved or deleted (the search session invalidates them).
  const { data: items, error } = useQuery<Conversation[]>('assistant.conversations');
  // The answers the search box is waiting for: their conversations say so, and a new conversation shows before it is
  // saved (the backend saves it with its answer), so that the user can come back to an answer still being written.
  const live = useLiveConversations();
  useEffect(() => {
    if (error) notify(error.message, true);
  }, [error, notify]);
  const current = search.state?.conversationId;
  const listed = new Set(items?.map((item) => item.id));
  const answering = new Set(
    live.flatMap((answer) =>
      answer.pending && answer.conversationId ? [answer.conversationId] : [],
    ),
  );
  // Until the list has it, an answer saved a moment ago stays here too: the list loads again right after. A new
  // conversation shows once, by its newest answer.
  const seen = new Set<string>();
  const fresh = live.filter(({ conversationId }) => {
    if (!conversationId) return true;
    if (listed.has(conversationId) || seen.has(conversationId)) return false;
    seen.add(conversationId);
    return true;
  });
  const count = items && items.length + fresh.length;
  return (
    <>
      <WidgetHead id="conversations" title="최근 대화" count={count} />
      {items && !count && (
        <p className="widget-note">위쪽 검색창에 질문하면 답변과 근거 문서가 대화로 저장됩니다.</p>
      )}
      <ul className="conversation-list">
        {fresh.map((answer) => (
          <li
            key={answer.requestId}
            className={
              answer.requestId === search.state?.requestId ||
              (!!answer.conversationId && answer.conversationId === current)
                ? 'current'
                : undefined
            }
          >
            <button
              className="conversation"
              onClick={() =>
                void search.follow(answer.requestId).catch((e) => notify(e.message, true))
              }
            >
              {answer.pending ? (
                <LoaderCircle className="spin" size={14} aria-hidden="true" />
              ) : (
                <MessageSquareText size={14} aria-hidden="true" />
              )}
              <strong>{answer.question}</strong>
              {answer.pending ? (
                <span className="conversation-state">답변 중</span>
              ) : (
                <time dateTime={new Date(answer.startedAt).toISOString()}>
                  {date(new Date(answer.startedAt).toISOString())}
                </time>
              )}
            </button>
          </li>
        ))}
        {items?.map((item) => (
          <li key={item.id} className={item.id === current ? 'current' : undefined}>
            <button
              className="conversation"
              onClick={() => void search.open(item).catch((e) => notify(e.message, true))}
            >
              {answering.has(item.id) ? (
                <LoaderCircle className="spin" size={14} aria-hidden="true" />
              ) : (
                <MessageSquareText size={14} aria-hidden="true" />
              )}
              <strong>{item.title}</strong>
              {answering.has(item.id) ? (
                <span className="conversation-state">답변 중</span>
              ) : (
                <time dateTime={item.updated_at}>{date(item.updated_at)}</time>
              )}
            </button>
            {/* The backend keeps a conversation that is answering: it is deleted once its answer is saved. */}
            <Confirm
              iconOnly
              name={`${item.title} 대화 삭제`}
              label="삭제"
              icon={<Trash2 size={14} />}
              question="대화와 근거 발췌를 삭제할까요?"
              disabled={answering.has(item.id)}
              onConfirm={async () => {
                try {
                  await api('assistant.delete', { id: item.id });
                  search.forget(item.id);
                } catch (e) {
                  notify((e as Error).message, true);
                }
              }}
            />
          </li>
        ))}
      </ul>
    </>
  );
}
