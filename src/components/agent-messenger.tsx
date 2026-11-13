import { useCallback, useEffect, useState } from 'react';
import { MessagesSquare } from 'lucide-react';
import {
  api,
  dayTime,
  type AgentMessengerConversation,
  type AgentMessengerMessage,
} from '@/lib/api';

// One conversation of the messenger gateway, its messages read when it is opened.
function Conversation({ conversation }: { conversation: AgentMessengerConversation }) {
  const [messages, setMessages] = useState<AgentMessengerMessage[] | null>(null);
  const [failed, setFailed] = useState('');
  const load = async () => {
    try {
      setMessages(
        await api<AgentMessengerMessage[]>('agent.messenger_messages', { key: conversation.key }),
      );
      setFailed('');
    } catch (error) {
      setFailed((error as Error).message);
    }
  };
  return (
    <details
      className="agent-messenger-conversation"
      onToggle={(event) => {
        if (event.currentTarget.open) void load();
      }}
    >
      <summary>
        <span className="record-kind">{conversation.label}</span>{' '}
        <strong>{conversation.account}</strong>
        {conversation.title && <span className="agent-card-meta"> · {conversation.title}</span>}
        <span className="agent-card-meta">
          {` · ${conversation.at ? `${dayTime(conversation.at)} · ` : ''}메시지 ${conversation.messages}개`}
        </span>
      </summary>
      {failed && <p className="inline-error">{failed}</p>}
      {messages && (
        <ol className="agent-messenger-messages" aria-label={`${conversation.account}와의 대화`}>
          {messages.map((message, index) => (
            <li key={index} className={`agent-message ${message.role}`}>
              <div className="agent-bubble">
                <p className="agent-text">{message.text}</p>
              </div>
              {message.at && <span className="agent-time">{dayTime(message.at)}</span>}
            </li>
          ))}
        </ol>
      )}
    </details>
  );
}

// What the accounts the user listed asked the agent in Telegram, Discord or Slack (the messenger gateway), shown in the
// chat to read: the conversations Hermes keeps a record of, each opened to its messages. Nothing here sends to a
// messenger. Shown only once there is a conversation.
export default function MessengerConversations() {
  const [conversations, setConversations] = useState<AgentMessengerConversation[]>([]);
  const load = useCallback(async () => {
    try {
      setConversations(await api<AgentMessengerConversation[]>('agent.messenger'));
    } catch {
      /* No Wiki is open yet, or Hermes keeps no record: nothing is shown. */
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  if (!conversations.length) return null;
  return (
    <details
      className="agent-messenger"
      onToggle={(event) => {
        if (event.currentTarget.open) void load();
      }}
    >
      <summary>
        <MessagesSquare size={14} aria-hidden="true" /> 메신저 대화 {conversations.length}개
      </summary>
      <p className="agent-footnote">
        메신저 게이트웨이로 받은 대화입니다. 여기서는 읽기만 하고, 답은 그 메신저에서 합니다.
      </p>
      {conversations.map((conversation) => (
        <Conversation key={conversation.key} conversation={conversation} />
      ))}
    </details>
  );
}
