import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Bot,
  CalendarClock,
  CalendarDays,
  Check,
  CircleAlert,
  CircleCheckBig,
  ExternalLink,
  FilePlus2,
  FileSearch,
  FileText,
  Globe2,
  HelpCircle,
  History,
  ListChecks,
  ListTodo,
  LoaderCircle,
  MessageSquare,
  Repeat,
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
  clock,
  dayTime,
  type AgentActivity,
  type AgentCard,
  type AgentClarify,
  type AgentDecision,
  type AgentEngine,
  type AgentApproval,
  type AgentMessage,
  type AgentTool,
  type AgentDocumentPart,
  type AgentOverview,
  type AgentRoutineLine,
  type AgentSuggestion,
  type AgentTaskLine,
  type TrashRestored,
  type Trashed,
} from '@/lib/api';
import MessengerConversations from './agent-messenger';
import { useChatFocus } from './chat-focus';
import Confirm from './confirm';
import EmptyState from './empty-state';
import { useActions, useAgentTurn, useStores, useUserOnlyNotices } from './state';

const TOOL_ICONS: Record<AgentTool, LucideIcon> = {
  search_documents: FileSearch,
  list_records: Table2,
  web_capture: ScanSearch,
  open_site: Globe2,
  capture_page: ScanSearch,
  add_task: ListChecks,
  schedule_capture: CalendarClock,
  list_tasks: ListTodo,
  complete_task: CircleCheckBig,
  read_document: FileText,
  save_document: FilePlus2,
  list_routines: Repeat,
  today: CalendarDays,
};
const STATUS: Record<AgentCard['status'], string> = {
  pending: '승인 대기',
  running: '실행 중',
  done: '완료',
  failed: '실패',
  cancelled: '취소함',
};
const KIND_NAMES = { ad: '광고', product: '상품', result: '검색 결과' } as const;
// What a step of a turn's activity was, and how it ended.
const STEP_KINDS = {
  tool: '도구',
  action: '앱 작업',
  approval: '명령 확인',
  question: '질문',
} as const;
const STEP_RESULTS: Record<string, string> = {
  once: '이번만 실행',
  session: '이 대화에서 허용',
  always: '항상 허용',
  deny: '거부',
  timeout: '응답 없음',
  stopped: '중지함',
  done: '완료',
  failed: '실패',
  submitted: '답함',
  cancelled: '답하지 않음',
};
// How the agent marks the choice it recommends (the first of a question's); shown in the app's language, sent as it is.
const RECOMMENDED = / \(Recommended\)$/;
// The results that mean the step did not go through.
const STEP_REFUSED = new Set(['deny', 'timeout', 'stopped', 'failed', 'cancelled']);
// The words of the task board and the routines widget.
const TASK_STATUS: Record<AgentTaskLine['status'], string> = {
  open: '진행 중',
  paused: '보류',
  done: '완료',
};
const ROUTINE_STATUS: Record<NonNullable<AgentRoutineLine['last_status']>, string> = {
  queued: '대기 중',
  running: '실행 중',
  success: '완료',
  error: '실패',
  cancelled: '중지',
};

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

// A dangerous command the agent paused on: the user allows it or refuses it, and the turn goes on. How far an answer
// reaches is said beside it: this run, this kind of command for the conversation, or this very command for good. A
// command the safety check itself refused can only be run this once, and one that is not a single plain command
// cannot be allowed for good. The decision is the user's alone: the whole chat page is out of an AI tool's reach
// (data-user-only on it).
function ApprovalPrompt({
  approval,
  onDecide,
}: {
  approval: AgentApproval;
  onDecide: (decision: AgentDecision) => void;
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
        {approval.allow_session && (
          <button className="button secondary small" onClick={() => onDecide('session')}>
            이 대화에서 허용
          </button>
        )}
        {approval.allow_permanent && (
          <button className="button secondary small" onClick={() => onDecide('always')}>
            항상 허용
          </button>
        )}
        <button className="button secondary small" onClick={() => onDecide('deny')}>
          <X size={14} /> 거부
        </button>
      </div>
      <ul className="agent-interaction-scopes">
        {approval.smart_denied && (
          <li>안전 검사가 위험하다고 본 명령입니다. 실행하더라도 이번 한 번뿐입니다.</li>
        )}
        {approval.allow_session && (
          <li>
            이 대화에서 허용: 대화를 비우거나 앱을 닫을 때까지 같은 종류의 명령을 다시 묻지
            않습니다.
          </li>
        )}
        {approval.allow_permanent && (
          <li>
            항상 허용: 글자까지 같은 이 명령만 앞으로 묻지 않습니다. 관리 → 채팅 에이전트에서 취소할
            수 있습니다.
          </li>
        )}
      </ul>
    </div>
  );
}

// What the turn an answer ended did on the way, folded under the answer: the tools the agent ran, the app's actions, the
// commands it asked about with how each was decided, and its questions.
function Activity({ activity }: { activity: AgentActivity | undefined }) {
  if (!activity?.steps.length) return null;
  return (
    <details className="agent-activity">
      <summary>
        <History size={13} aria-hidden="true" /> 한 일 {activity.steps.length + activity.more}건
      </summary>
      <ol aria-label="이 답을 만들며 한 일">
        {activity.steps.map((step, index) => (
          <li key={index} className={step.kind}>
            <span className="agent-step-kind">{STEP_KINDS[step.kind] ?? step.kind}</span>
            <span className="agent-step-text" title={step.note || undefined}>
              {step.text}
            </span>
            {step.result && (
              <span
                className={`agent-step-result ${STEP_REFUSED.has(step.result) ? 'refused' : ''}`}
              >
                {STEP_RESULTS[step.result] ?? step.result}
              </span>
            )}
          </li>
        ))}
      </ol>
      {activity.more > 0 && (
        <p className="agent-activity-more">그 뒤의 {activity.more}건은 적지 않았습니다.</p>
      )}
    </details>
  );
}

// The questions the agent asked to go on: one of the choices, or an answer in the user's own words, each.
function ClarifyPrompt({
  clarify,
  onSubmit,
}: {
  clarify: AgentClarify;
  onSubmit: (answers: Record<string, string>) => void;
}) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  // The answers written out by hand, by question: one of those is sent in place of a choice.
  const [written, setWritten] = useState<Record<string, string>>({});
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
            <>
              <div className="agent-clarify-choices" role="group" aria-label={question.question}>
                {question.choices.map((choice) => (
                  <button
                    type="button"
                    key={choice}
                    aria-pressed={!written[question.qid] && answers[question.qid] === choice}
                    className={`button small ${
                      !written[question.qid] && answers[question.qid] === choice
                        ? 'primary'
                        : 'secondary'
                    }`}
                    onClick={() => {
                      setWritten((current) => ({ ...current, [question.qid]: '' }));
                      setAnswers((current) => ({ ...current, [question.qid]: choice }));
                    }}
                  >
                    {choice.replace(RECOMMENDED, ' (추천)')}
                  </button>
                ))}
              </div>
              <input
                className="agent-clarify-input"
                aria-label={`${question.question} (직접 입력)`}
                placeholder="보기에 없으면 직접 적어 주세요"
                maxLength={2000}
                value={written[question.qid] ?? ''}
                onChange={(event) => {
                  const typed = event.target.value;
                  setWritten((current) => ({ ...current, [question.qid]: typed }));
                  setAnswers((current) => {
                    const next = { ...current };
                    if (typed.trim()) next[question.qid] = typed.trim();
                    else delete next[question.qid];
                    return next;
                  });
                }}
              />
            </>
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

// Tasks as the app tools list them: the due time on this PC's clock, and whether it has passed.
function TaskLines({ label, tasks }: { label: string; tasks: AgentTaskLine[] }) {
  return (
    <ul className="agent-card-list" aria-label={label}>
      {tasks.map((task) => (
        <li key={task.id}>
          {task.title}
          <span className={`agent-card-meta${task.overdue ? ' overdue' : ''}`}>
            {' · '}
            {task.overdue && '기한 지남 · '}
            {task.due_at ? `마감 ${dayTime(task.due_at)}` : '기한 없음'} ·{' '}
            {TASK_STATUS[task.status]}
          </span>
        </li>
      ))}
    </ul>
  );
}

function RoutineLines({ routines }: { routines: AgentRoutineLine[] }) {
  return (
    <ul className="agent-card-list" aria-label="자동 작업">
      {routines.map((routine) => (
        <li key={routine.id}>
          {routine.name}
          <span className="agent-card-meta">
            {` · ${routine.schedule} · ${routine.enabled ? '켜짐' : '꺼짐'}`}
            {routine.last_status && ` · 마지막 실행 ${ROUTINE_STATUS[routine.last_status]}`}
            {routine.last_status && routine.last_run_at && ` (${dayTime(routine.last_run_at)})`}
          </span>
        </li>
      ))}
    </ul>
  );
}

// The day at a glance (the today tool), each line with its kind; the counts are in the line above it.
function Overview({ overview }: { overview: AgentOverview }) {
  const { openDocument } = useActions();
  const tasks = [
    ...overview.overdue.tasks.map((task) => ({ kind: '기한 지남', task })),
    ...overview.due_today.tasks.map((task) => ({ kind: '오늘 마감', task })),
  ];
  if (!tasks.length && !overview.routines.items.length && !overview.documents.items.length)
    return null;
  return (
    <ul className="agent-card-list" aria-label="오늘 현황">
      {tasks.map(({ kind, task }) => (
        <li key={task.id}>
          <span className="record-kind">{kind}</span> {task.title}
          {task.due_at && <span className="agent-card-meta"> · {dayTime(task.due_at)}</span>}
        </li>
      ))}
      {overview.routines.items.map((routine) => (
        <li key={routine.id}>
          <span className="record-kind">자동 작업</span> {routine.name}
          {routine.next_run_at && (
            <span className="agent-card-meta"> · {clock(routine.next_run_at)}</span>
          )}
        </li>
      ))}
      {overview.documents.items.map((doc) => (
        <li key={doc.id}>
          <span className="record-kind">새 문서</span>{' '}
          <button className="link-button" onClick={() => openDocument(doc.id)}>
            {doc.title}
          </button>
        </li>
      ))}
    </ul>
  );
}

// The document a card read or saved, opened in the document window.
function DocumentLink({ doc }: { doc: AgentDocumentPart }) {
  const { openDocument } = useActions();
  return (
    <button className="link-button" onClick={() => openDocument(doc.id)}>
      <FileText size={14} /> {doc.title} 열기
    </button>
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
      {!!result.tasks?.length && <TaskLines label="할 일" tasks={result.tasks} />}
      {!!result.routines?.length && <RoutineLines routines={result.routines} />}
      {result.overview && <Overview overview={result.overview} />}
      {result.document && <DocumentLink doc={result.document} />}
      {result.capture_id && (
        <button
          className="button secondary small"
          onClick={() => navigate('records', `capture:${result.capture_id}`)}
        >
          <Table2 size={14} /> 웹 기록에서 보기
        </button>
      )}
      {(!!result.tasks || !!result.task_id) && (
        <button className="button secondary small" onClick={() => navigate('tasks')}>
          <ListChecks size={14} /> 할 일 보기
        </button>
      )}
      {(result.routine_id || !!result.routines?.length) && (
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

// What to ask next, offered above the message box from what the app keeps now (agent.suggestions): tasks past their
// due time or due today, a routine that failed, new documents, today's web records; examples when there is nothing to
// say. Read each time the chips are shown: when the chat is, and once a turn has ended (they are hidden while it runs).
// A chip only fills the box, for the user to read, change and send.
function Suggestions({ onPick }: { onPick: (text: string) => void }) {
  const [offers, setOffers] = useState<AgentSuggestion[]>([]);
  useEffect(() => {
    let shown = true;
    void api<AgentSuggestion[]>('agent.suggestions')
      .then((found) => {
        if (shown) setOffers(found);
      })
      // The box works all the same without them; there is nothing to tell the user.
      .catch(() => {});
    return () => {
      shown = false;
    };
  }, []);
  if (!offers.length) return null;
  return (
    <div className="agent-suggestions" role="group" aria-label="제안">
      {offers.map((offer) => (
        <button
          key={offer.text}
          type="button"
          className="agent-suggestion"
          title={offer.reason || undefined}
          onClick={() => onPick(offer.text)}
        >
          {offer.text}
        </button>
      ))}
    </div>
  );
}

export default function AgentChat() {
  // What the chat tells, its 되돌리기 too, is the user's alone as the chat is.
  const { notify, undoable } = useUserOnlyNotices('chat');
  // The turn in progress lives outside this screen (agent-turn.ts): it goes on while another screen is shown, and is
  // shown again, with its progress and the question it waits on, when the chat is.
  const { turn } = useStores();
  const sending = useAgentTurn((state) => state.sending);
  const status = useAgentTurn((state) => state.status);
  const ended = useAgentTurn((state) => state.ended);
  const failed = useAgentTurn((state) => state.failed);
  const [messages, setMessages] = useState<AgentMessage[] | null>(null);
  const [engine, setEngine] = useState<AgentEngine | null>(null);
  const [text, setText] = useState('');
  const list = useRef<HTMLOListElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  useChatFocus(input);
  // The turns that ended before this screen was shown are in the conversation it loads.
  const taken = useRef(ended.n);
  // Only the conversation read last is shown: one asked for earlier may answer later, without what was saved since.
  const loads = useRef(0);

  const load = useCallback(async () => {
    const mine = ++loads.current;
    try {
      const [loaded, planner] = await Promise.all([
        api<AgentMessage[]>('agent.messages', { limit: 100 }),
        api<AgentEngine>('agent.engine'),
      ]);
      if (mine !== loads.current) return;
      setMessages(loaded);
      setEngine(planner);
    } catch (error) {
      if (mine !== loads.current) return;
      setMessages([]);
      notify(error instanceof Error ? error.message : String(error), true);
    }
  }, [notify]);

  // The conversation as it is saved, and a turn that runs although nothing follows it (the window was loaded again).
  useEffect(() => {
    void load();
    void turn.resume();
  }, [load, turn]);

  // A turn ended: the two messages it saved join the conversation at once, and the conversation is read again, which
  // also brings the messages of a turn this screen did not send itself (one found after a reload).
  useEffect(() => {
    if (ended.n === taken.current) return;
    taken.current = ended.n;
    const sent = ended.sent;
    if (sent)
      setMessages((current) => [
        ...(current ?? []).filter(
          (item) => item.seq !== sent.user.seq && item.seq !== sent.reply.seq,
        ),
        sent.user,
        sent.reply,
      ]);
    void load();
    input.current?.focus();
  }, [ended, load]);

  // A request that failed gives its words back to the box, unless the user has written on meanwhile.
  useEffect(() => {
    if (failed === null) return;
    setText((current) => current || failed);
    turn.takeFailed();
    input.current?.focus();
  }, [failed, turn]);

  useEffect(() => {
    list.current?.lastElementChild?.scrollIntoView({ block: 'end', behavior: 'instant' });
  }, [messages, sending, status]);

  const replace = (message: AgentMessage) =>
    setMessages((current) =>
      (current ?? []).map((item) => (item.seq === message.seq ? message : item)),
    );

  const send = (words: string) => {
    const message = words.trim();
    if (!message || sending) return;
    setText('');
    void turn.send(message);
  };
  // The user's words are saved as the turn starts: a conversation read meanwhile (the chat was left and shown again)
  // already ends with them, and they are not drawn a second time.
  const last = messages?.at(-1);
  const saved = !!sending && last?.role === 'user' && last.content === sending.text;

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

  const clear = async () => {
    // A conversation read before this one was cleared must not come back as it answers.
    loads.current += 1;
    const { trash_ids: ids } = await api<Trashed>('agent.clear');
    setMessages([]);
    // It waits in the trash, from where 되돌리기 brings the messages back; what was allowed for it stays forgotten.
    if (ids.length)
      undoable('대화를 휴지통으로 옮겼습니다.', {
        label: '되돌리기',
        run: async () => {
          try {
            const { restored, notes } = await api<TrashRestored>('trash.restore', { ids });
            if (!restored)
              notify('휴지통에 없는 대화입니다. 이미 복원했거나 영구 삭제했습니다.', true);
            for (const note of notes) notify(note, false, true);
          } catch (error) {
            notify(error instanceof Error ? error.message : String(error), true);
          }
          await load();
        },
      });
  };

  // The user's alone (data-user-only), its heading with 대화 비우기 too: what is typed here is carried out by an agent
  // with a terminal, files and a browser on this PC, and the answer to its question before a dangerous command is the
  // user's decision. The local API's screen control (app-control.ts) types and clicks nothing on this page, and neither
  // reads nor captures it, at any access level.
  return (
    <div className="workspace-page agent-page" data-user-only="chat">
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
              question="대화를 모두 휴지통으로 옮길까요? 답마다 남긴 ‘한 일’도 함께 옮기고, 30일 안에 복원할 수 있습니다. 이 대화에서 허용한 명령은 복원해도 다시 묻습니다. 웹 기록과 할 일은 그대로 둡니다."
              icon={<Trash2 size={15} />}
              onConfirm={clear}
              disabled={!!sending}
            />
          )}
        </div>
      </header>
      <section className="agent-chat" aria-label="에이전트와의 대화">
        <MessengerConversations />
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
                  <Activity activity={message.activity} />
                </div>
                <span className="agent-time">{dayTime(message.created_at)}</span>
              </li>
            ))}
            {sending && (
              <>
                {!saved && (
                  <li className="agent-message user">
                    <div className="agent-bubble">
                      <p className="agent-text">{sending.text}</p>
                    </div>
                  </li>
                )}
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
                      <ApprovalPrompt
                        approval={status.interaction}
                        onDecide={(decision) => void turn.decide(decision)}
                      />
                    )}
                    {status?.interaction?.kind === 'clarify' && (
                      <ClarifyPrompt
                        key={status.interaction.interaction_id}
                        clarify={status.interaction}
                        onSubmit={(answers) => void turn.answer(answers)}
                      />
                    )}
                  </div>
                </li>
              </>
            )}
          </ol>
        )}
        {!sending && (
          <Suggestions
            onPick={(words) => {
              setText(words);
              input.current?.focus();
            }}
          />
        )}
        <form
          className="agent-compose"
          onSubmit={(event) => {
            event.preventDefault();
            send(text);
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
                send(text);
              }
            }}
          />
          {sending ? (
            <button
              type="button"
              className="button secondary"
              onClick={() => void turn.stop()}
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
