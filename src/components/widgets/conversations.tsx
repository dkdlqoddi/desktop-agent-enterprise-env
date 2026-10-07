import { useEffect, useState } from 'react';
import { MessageSquareText, Trash2 } from 'lucide-react';
import { api, date, type Conversation } from '@/lib/api';
import { useApp } from '../app';
import Confirm from '../confirm';
import { WidgetHead } from '../dashboard';

export default function ConversationsWidget() {
  const { search, notify, version } = useApp();
  const [items, setItems] = useState<Conversation[] | null>(null);
  useEffect(() => {
    let live = true;
    api<Conversation[]>('assistant.conversations')
      .then((value) => live && setItems(value))
      .catch((error) => live && notify(error.message, true));
    return () => {
      live = false;
    };
  }, [search.saved, version, notify]);
  const current = search.state?.conversationId;
  return (
    <>
      <WidgetHead id="conversations" title="최근 대화" count={items?.length} />
      {items && !items.length && (
        <p className="widget-note">위쪽 검색창에 질문하면 답변과 근거 문서가 대화로 저장됩니다.</p>
      )}
      <ul className="conversation-list">
        {items?.map((item) => (
          <li key={item.id} className={item.id === current ? 'current' : undefined}>
            <button
              className="conversation"
              onClick={() => void search.open(item).catch((e) => notify(e.message, true))}
            >
              <MessageSquareText size={14} aria-hidden="true" />
              <strong>{item.title}</strong>
              <time dateTime={item.updated_at}>{date(item.updated_at)}</time>
            </button>
            <Confirm
              iconOnly
              name={`${item.title} 대화 삭제`}
              label="삭제"
              icon={<Trash2 size={14} />}
              question="대화와 근거 발췌를 삭제할까요?"
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
