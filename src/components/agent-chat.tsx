import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Bot,
  CalendarClock,
  Check,
  CircleAlert,
  ExternalLink,
  FileSearch,
  Globe2,
  HelpCircle,
  ListChecks,
  LoaderCircle,
  MessageSquare,
  ScanSearch,
  Send,
  ShieldAlert,
  Square,
  Table2,
  Terminal,
  Trash2,
  X,
  type LucideIcon,
} from 'lucide-react';
import {
  api,
  dayTime,
  type AgentCard,
  type AgentClarify,
  type AgentEngine,
  type AgentApproval,
  type AgentMessage,
  type AgentSend,
  type AgentStatus,
  type AgentTool,
} from '@/lib/api';
import Confirm from './confirm';
import EmptyState from './empty-state';
import { useActions } from './state';

const TOOL_ICONS: Record<AgentTool, LucideIcon> = {
  search_documents: FileSearch,
  list_records: Table2,
  web_capture: ScanSearch,
  open_site: Globe2,
  capture_page: ScanSearch,
  add_task: ListChecks,
  schedule_capture: CalendarClock,
};
const STATUS: Record<AgentCard['status'], string> = {
  pending: '승인 대기',
  running: '실행 중',
  done: '완료',
  failed: '실패',
  cancelled: '취소함',
};
const EXAMPLES = [
  '쿠팡에서 노트북 광고 기록해 줘',
  '아마존에서 wireless earbuds 검색해서 광고 보여 줘',
  '네이버에서 캠핑의자 광고 기록해 줘',
  '오늘 기록한 광고 요약해 줘',
];
const KIND_NAMES = { ad: '광고', product: '상품', result: '검색 결과' } as const;

function requestId() {
  return `chat-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function EngineBadge({ engine }: { engine: AgentEngine | null }) {
  if (!engine) return null;
  const [label, title] = !engine.ready
    ? ['Hermes 준비 안 됨', '내장 Hermes 실행 환경이 없습니다. 앱 배포 파일을 다시 준비해 주세요.']
    : engine.engine === 'hermes'
      ? [
          'Hermes',
          '내장 Hermes 에이전트가 직접 도구를 써서 작업을 실행합니다. 위험한 명령만 확인을 받습니다.',
        ]
      : [
          '기본 계획기',
          '내장 Hermes가 없는 개발 실행입니다. 연결한 LLM이 작업을 카드로 제안만 합니다. npm run build:hermes로 Hermes를 준비할 수 있습니다.',
        ];
  return (
    <span className={`agent-engine ${engine.ready ? '' : 'warn'}`} title={title}>
      <Bot size={14} /> {label}
    </span>
  );
}

// A dangerous command the agent paused on: the user allows it (once/always) or refuses it, and the turn goes on.
function ApprovalPrompt({
  approval,
  onDecide,
}: {
  approval: AgentApproval;
  onDecide: (decision: 'once' | 'always' | 'deny') => void;
}) {
  return (
    <div className="agent-interaction approval" role="group" aria-label="명령 실행 확인">
      <p className="agent-interaction-head">
        <ShieldAlert size={15} /> 이 명령을 실행할까요?
      </p>
      <pre className="agent-command">
        <Terminal size={13} aria-hidden="true" /> {approval.command}
      </pre>
      {approval.description && <p className="agent-interaction-note">{approval.description}</p>}
      <div className="agent-card-actions">
        <button className="button primary small" onClick={() => onDecide('once')}>
          <Check size={14} /> 이번만 실행
        </button>
        <button className="button secondary small" onClick={() => onDecide('always')}>
          항상 허용
        </button>
        <button
          className="button secondary small"
          data-destructive
          onClick={() => onDecide('deny')}
        >
          <X size={14} /> 거부
        </button>
      </div>
    </div>
  );
}

// The questions the agent asked to go on: one choice or a short answer each.
function ClarifyPrompt({
  clarify,
  onSubmit,
}: {
  clarify: AgentClarify;
  onSubmit: (answers: Record<string, string>) => void;
}) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  return (
    <form
      className="agent-interaction clarify"
      aria-label="추가 질문"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit(answers);
      }}
    >
      <p className="agent-interaction-head">
        <HelpCircle size={15} /> 조금 더 알려 주세요
      </p>
      {clarify.questions.map((question) => (
        <div key={question.qid} className="agent-clarify-question">
          <p>{question.question}</p>
          {question.choices?.length ? (
            <div className="agent-clarify-choices" role="group" aria-label={question.question}>
              {question.choices.map((choice) => (
                <button
                  type="button"
                  key={choice}
                  className={`button small ${answers[question.qid] === choice ? 'primary' : 'secondary'}`}
                  onClick={() => setAnswers((current) => ({ ...current, [question.qid]: choice }))}
                >
                  {choice}
                </button>
              ))}
            </div>
          ) : (
            <input
              className="agent-clarify-input"
              aria-label={question.question}
              value={answers[question.qid] ?? ''}
              onChange={(event) =>
                setAnswers((current) => ({ ...current, [question.qid]: event.target.value }))
              }
            />
          )}
        </div>
      ))}
      <div className="agent-card-actions">
        <button type="submit" className="button primary small">
          <Check size={14} /> 보내기
        </button>
      </div>
    </form>
  );
}

function CardResult({ card }: { card: AgentCard }) {
  const { openDocument, navigate } = useActions();
  const result = card.result;
  if (!result) return null;
  return (
    <div className="agent-card-result">
      <p>{result.text}</p>
      {!!result.ads?.length && (
        <ul className="agent-card-list" aria-label="기록한 광고">
          {result.ads.map((title, index) => (
            <li key={index}>
              <span className="record-kind ad">광고</span> {title}
            </li>
          ))}
        </ul>
      )}
      {!!result.documents?.length && (
        <ul className="agent-card-list" aria-label="찾은 문서">
          {result.documents.map((doc) => (
            <li key={doc.id}>
              <button className="link-button" onClick={() => openDocument(doc.id)}>
                {doc.title}
              </button>
            </li>
          ))}
        </ul>
      )}
      {!!result.records?.length && (
        <ul className="agent-card-list" aria-label="웹 기록">
          {result.records.map((record, index) => (
            <li key={index}>
              <span className={`record-kind ${record.kind}`}>{KIND_NAMES[record.kind]}</span>{' '}
              {record.title}
              {record.price && <span className="agent-price"> · {record.price}</span>}
            </li>
          ))}
        </ul>
      )}
      {result.capture_id && (
        <button
          className="button secondary small"
          onClick={() => navigate('records', `capture:${result.capture_id}`)}
        >
          <Table2 size={14} /> 웹 기록에서 보기
        </button>
      )}
      {result.routine_id && (
        <button
          className="button secondary small"
          onClick={() => navigate('automation', 'routines')}
        >
          <CalendarClock size={14} /> 자동 작업 보기
        </button>
      )}
    </div>
  );
}

function ActionCard({
  card,
  onApprove,
  onReject,
}: {
  card: AgentCard;
  onApprove: () => void;
  onReject: () => void;
}) {
  const Icon = TOOL_ICONS[card.tool] ?? Bot;
  return (
    <div className={`agent-card ${card.status}`}>
      <div className="agent-card-head">
        <span className="agent-card-icon" aria-hidden="true">
          <Icon size={16} />
        </span>
        <strong>{card.title}</strong>
        <span className={`agent-card-status ${card.status}`}>
          {card.status === 'running' && <LoaderCircle className="spin" size={13} />}
          {card.status === 'done' && <Check size={13} />}
          {card.status === 'failed' && <CircleAlert size={13} />}
          {STATUS[card.status]}
        </span>
      </div>
      {card.status === 'pending' && card.approval && (
        <div className="agent-card-actions">
          <button className="button primary small" onClick={onApprove}>
            <Check size={14} /> 확인 후 실행
          </button>
          <button className="button secondary small" onClick={onReject}>
            <X size={14} /> 취소
          </button>
          {(card.tool === 'web_capture' || card.tool === 'open_site') && (
            <span className="agent-card-note">
              브라우저 창이 열립니다. 앱은 광고를 누르지 않습니다.
            </span>
          )}
        </div>
      )}
      <CardResult card={card} />
    </div>
  );
}

export default function AgentChat() {
  const { notify } = useActions();
  const [messages, setMessages] = useState<AgentMessage[] | null>(null);
  const [engine, setEngine] = useState<AgentEngine | null>(null);
  const [text, setText] = useState('');
  const [sending, setSending] = useState<{ id: string; text: string } | null>(null);
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const list = useRef<HTMLOListElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  const load = useCallback(async () => {
    try {
      const [loaded, planner] = await Promise.all([
        api<AgentMessage[]>('agent.messages', { limit: 100 }),
        api<AgentEngine>('agent.engine'),
      ]);
      setMessages(loaded);
      setEngine(planner);
    } catch (error) {
      setMessages([]);
      notify(error instanceof Error ? error.message : String(error), true);
    }
  }, [notify]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    list.current?.lastElementChild?.scrollIntoView({ block: 'end', behavior: 'instant' });
  }, [messages, sending, status]);

  // While Hermes plans, what it writes shows as a draft (never the plan itself), and 중지 stops it.
  useEffect(() => {
    if (!sending) return;
    let stopped = false;
    const timer = setInterval(() => {
      api<AgentStatus>('agent.status', { request_id: sending.id })
        .then((next) => {
          if (!stopped) setStatus(next);
        })
        // The answer comes with the request itself; a status that does not is only a draft missed.
        .catch(() => {});
    }, 700);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [sending]);

  const replace = (message: AgentMessage) =>
    setMessages((current) =>
      (current ?? []).map((item) => (item.seq === message.seq ? message : item)),
    );

  const send = async (words: string) => {
    const message = words.trim();
    if (!message || sending) return;
    const id = requestId();
    setSending({ id, text: message });
    setStatus(null);
    setText('');
    try {
      const sent = await api<AgentSend>('agent.send', { text: message, request_id: id });
      setMessages((current) => [...(current ?? []), sent.user, sent.reply]);
    } catch (error) {
      setText(message);
      notify(error instanceof Error ? error.message : String(error), true);
    } finally {
      setSending(null);
      setStatus(null);
      input.current?.focus();
    }
  };

  const act = async (message: AgentMessage, index: number, approve: boolean) => {
    const card = message.actions[index];
    replace({
      ...message,
      actions: message.actions.map((item, i) =>
        i === index ? { ...card, status: approve ? 'running' : 'cancelled' } : item,
      ),
    });
    try {
      replace(
        await api<AgentMessage>(approve ? 'agent.approve' : 'agent.reject', {
          seq: message.seq,
          index,
        }),
      );
    } catch (error) {
      notify(error instanceof Error ? error.message : String(error), true);
      await load();
    }
  };

  const stop = async () => {
    if (!sending) return;
    try {
      await api('agent.stop', { request_id: sending.id });
    } catch (error) {
      notify(error instanceof Error ? error.message : String(error), true);
    }
  };

  // The user's answer to an approval or clarify the agent paused on; the turn then goes on.
  const decide = async (decision: 'once' | 'always' | 'deny') => {
    const interaction = status?.interaction;
    if (!sending || interaction?.kind !== 'approval') return;
    setStatus((current) => (current ? { ...current, interaction: null } : current));
    try {
      await api('agent.approve_command', {
        request_id: sending.id,
        interaction_id: interaction.interaction_id,
        decision,
      });
    } catch (error) {
      notify(error instanceof Error ? error.message : String(error), true);
    }
  };

  const answerClarify = async (answers: Record<string, string>) => {
    const interaction = status?.interaction;
    if (!sending || interaction?.kind !== 'clarify') return;
    setStatus((current) => (current ? { ...current, interaction: null } : current));
    try {
      await api('agent.answer_clarify', {
        request_id: sending.id,
        interaction_id: interaction.interaction_id,
        answers,
      });
    } catch (error) {
      notify(error instanceof Error ? error.message : String(error), true);
    }
  };

  const clear = async () => {
    await api('agent.clear');
    setMessages([]);
  };

  return (
    <div className="workspace-page agent-page">
      <header className="page-heading">
        <div>
          <p className="eyebrow">AGENT</p>
          <h1>채팅</h1>
          <p>말로 맡기면 에이전트가 바로 처리하고, 위험한 명령만 확인을 받습니다.</p>
        </div>
        <div className="agent-heading-tools">
          <EngineBadge engine={engine} />
          {!!messages?.length && (
            <Confirm
              label="대화 비우기"
              question="대화를 모두 지울까요? 웹 기록과 할 일은 그대로 둡니다."
              icon={<Trash2 size={15} />}
              onConfirm={clear}
              disabled={!!sending}
            />
          )}
        </div>
      </header>
      <section className="agent-chat" aria-label="에이전트와의 대화">
        {messages === null ? (
          <div className="agent-loading">
            <LoaderCircle className="spin" size={22} /> 대화를 불러오는 중
          </div>
        ) : messages.length === 0 && !sending ? (
          <EmptyState icon={MessageSquare} title="무엇을 맡길까요?">
            사이트를 대신 열어 보이는 광고와 상품을 기록하거나, 문서를 찾고 할 일을 추가합니다.
            브라우저 창은 눈앞에 열리고, 앱은 광고를 누르지 않습니다.
          </EmptyState>
        ) : (
          <ol className="agent-messages" ref={list} role="log" aria-live="polite" aria-label="대화">
            {messages.map((message) => (
              <li key={message.seq} className={`agent-message ${message.role}`}>
                <div className="agent-bubble">
                  {message.content && <p className="agent-text">{message.content}</p>}
                  {message.actions.map((card, index) => (
                    <ActionCard
                      key={index}
                      card={card}
                      onApprove={() => void act(message, index, true)}
                      onReject={() => void act(message, index, false)}
                    />
                  ))}
                </div>
                <span className="agent-time">{dayTime(message.created_at)}</span>
              </li>
            ))}
            {sending && (
              <>
                <li className="agent-message user">
                  <div className="agent-bubble">
                    <p className="agent-text">{sending.text}</p>
                  </div>
                </li>
                <li className="agent-message assistant pending">
                  <div className="agent-bubble">
                    {!!status?.progress?.length && (
                      <ul className="agent-progress" aria-label="진행한 작업">
                        {status.progress.slice(-6).map((step, index) => (
                          <li key={index} className={step.event}>
                            {step.event === 'tool.completed' ? (
                              <Check size={12} />
                            ) : (
                              <LoaderCircle className="spin" size={12} />
                            )}{' '}
                            {step.text}
                          </li>
                        ))}
                      </ul>
                    )}
                    <p className="agent-text">
                      <LoaderCircle className="spin" size={14} />{' '}
                      {status?.draft || status?.text || '작업을 진행하고 있습니다.'}
                    </p>
                    {status?.interaction?.kind === 'approval' && (
                      <ApprovalPrompt approval={status.interaction} onDecide={decide} />
                    )}
                    {status?.interaction?.kind === 'clarify' && (
                      <ClarifyPrompt
                        key={status.interaction.interaction_id}
                        clarify={status.interaction}
                        onSubmit={answerClarify}
                      />
                    )}
                  </div>
                </li>
              </>
            )}
          </ol>
        )}
        {!messages?.length && !sending && (
          <div className="agent-examples" role="group" aria-label="예시">
            {EXAMPLES.map((example) => (
              <button key={example} className="agent-example" onClick={() => void send(example)}>
                {example}
              </button>
            ))}
          </div>
        )}
        <form
          className="agent-compose"
          onSubmit={(event) => {
            event.preventDefault();
            void send(text);
          }}
        >
          <textarea
            ref={input}
            aria-label="메시지"
            placeholder="예) 쿠팡에서 노트북 광고 기록해 줘 (Enter 보내기, Shift+Enter 줄바꿈)"
            value={text}
            rows={2}
            maxLength={4000}
            disabled={!!sending}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                void send(text);
              }
            }}
          />
          {sending ? (
            <button
              type="button"
              className="button secondary"
              onClick={() => void stop()}
              disabled={status ? !status.can_stop : false}
            >
              <Square size={15} /> 중지
            </button>
          ) : (
            <button type="submit" className="button primary" disabled={!text.trim()}>
              <Send size={15} /> 보내기
            </button>
          )}
        </form>
        <p className="agent-footnote">
          <ExternalLink size={12} /> 사이트 작업은 별도 브라우저 창에서 보이게 진행됩니다. 직접 누른
          광고만 도착 페이지와 함께 기록됩니다.
        </p>
      </section>
    </div>
  );
}
