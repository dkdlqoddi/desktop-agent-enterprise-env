import type { AgentActive, AgentSend, AgentStatus, Method } from '@/lib/api';
import type { Store } from '@/lib/store';

// The chat's turn in progress, kept apart from the chat screen. A turn of the agent takes minutes and may stop to ask
// the user (a dangerous command, a clarify question): were it held by the screen, leaving the chat for another screen
// would lose the request, its progress, the question and the way to stop it, and the question would run out its time
// unanswered. Here the request goes on while the user is elsewhere, the chat shows it again when it is opened, and a
// message tells the user that the agent waits for them or has ended. A chat loaded again while a turn runs (the window
// was reloaded) finds the turn in the backend (agent.active) and follows it the same way.
export type AgentTurnState = {
  // The request being carried out and the user's words.
  sending: { id: string; text: string } | null;
  // What the turn is doing, as agent.status last told; null until it has.
  status: AgentStatus | null;
  // The turn that ended last, counted so that the screen takes it once. `sent`: the two messages it saved, or null when
  // the request was not this screen's own (found after a reload) and the conversation is to be read again.
  ended: { n: number; sent: AgentSend | null };
  // The words of a request that failed, for the box they were typed in; taken once (takeFailed).
  failed: string | null;
};

export const NO_TURN: AgentTurnState = {
  sending: null,
  status: null,
  ended: { n: 0, sent: null },
  failed: null,
};

// How often a running turn is asked what it is doing.
export const POLL_MS = 700;
const WAITING = '채팅 에이전트가 확인을 기다리고 있습니다. 채팅에서 답해 주세요.';
const ENDED = '채팅 에이전트가 작업을 마쳤습니다.';

const words = (error: unknown) => (error instanceof Error ? error.message : String(error));

export function newTurnId() {
  return `chat-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

type Deps = {
  store: Store<AgentTurnState>;
  request: <T>(method: Method, params?: Record<string, unknown>) => Promise<T>;
  notify: (message: string, error?: boolean) => void;
  // A message with a button that opens the chat, for a user on another screen. `waits`: the agent waits for the user,
  // so the message stays until it is read.
  prompt: (message: string, waits: boolean) => void;
  // Whether the chat is the screen shown.
  shown: () => boolean;
  // Runs `task` every `ms` until the function it returns is called.
  every?: (task: () => void, ms: number) => () => void;
};

export class AgentTurn {
  readonly store: Store<AgentTurnState>;
  private stopPolling: (() => void) | null = null;
  private polling = false;
  // The requests whose own answer is still awaited: such a turn ends with its answer, not with a status.
  private awaited = new Set<string>();
  // The question the user was last told about while on another screen.
  private told = '';

  constructor(private deps: Deps) {
    this.store = deps.store;
  }

  private get state() {
    return this.store.get();
  }

  // Sends the user's words and follows the turn to its end. Settles when the turn has ended or failed; a failure is
  // told to the user and its words are kept for the box (takeFailed).
  async send(text: string, id = newTurnId()) {
    if (this.state.sending) return;
    this.begin({ id, text });
    this.awaited.add(id);
    try {
      const sent = await this.deps.request<AgentSend>('agent.send', { text, request_id: id });
      this.end(id, sent);
    } catch (error) {
      // The request can end without its answer while the turn goes on (the main process gives up on a request that
      // waited long for the user's answers): the turn is then followed like one found after a reload.
      if (await this.running(id)) return;
      this.fail(id, text, error);
    } finally {
      this.awaited.delete(id);
    }
  }

  // Finds a turn that runs although nothing here follows it: the screen was loaded again meanwhile.
  async resume() {
    if (this.state.sending) return;
    let active: AgentActive;
    try {
      active = await this.deps.request<AgentActive>('agent.active');
    } catch {
      return; // The chat then shows the conversation as it is saved.
    }
    if (!active?.request_id || this.state.sending) return;
    this.begin({ id: active.request_id, text: active.text });
  }

  // 중지: the backend stops the turn, whose request then answers that it was stopped.
  async stop() {
    const sending = this.state.sending;
    if (!sending) return;
    try {
      await this.deps.request('agent.stop', { request_id: sending.id });
    } catch (error) {
      this.deps.notify(words(error), true);
    }
  }

  // The user's decision on the dangerous command the turn paused on; the turn then goes on.
  async decide(decision: 'once' | 'always' | 'deny') {
    const sending = this.state.sending;
    const interaction = this.state.status?.interaction;
    if (!sending || interaction?.kind !== 'approval') return;
    this.answered();
    try {
      await this.deps.request('agent.approve_command', {
        request_id: sending.id,
        interaction_id: interaction.interaction_id,
        decision,
      });
    } catch (error) {
      this.deps.notify(words(error), true);
    }
  }

  // The user's answers to the questions the turn paused on.
  async answer(answers: Record<string, string>) {
    const sending = this.state.sending;
    const interaction = this.state.status?.interaction;
    if (!sending || interaction?.kind !== 'clarify') return;
    this.answered();
    try {
      await this.deps.request('agent.answer_clarify', {
        request_id: sending.id,
        interaction_id: interaction.interaction_id,
        answers,
      });
    } catch (error) {
      this.deps.notify(words(error), true);
    }
  }

  // The words of the request that failed last, once: the chat puts them back into its box.
  takeFailed() {
    const failed = this.state.failed;
    if (failed !== null) this.store.set((state) => ({ ...state, failed: null }));
    return failed;
  }

  // The question is taken off the screen as it is answered; the next status tells what the turn does then.
  private answered() {
    this.store.set((state) =>
      state.status ? { ...state, status: { ...state.status, interaction: null } } : state,
    );
  }

  private begin(sending: { id: string; text: string }) {
    this.told = '';
    this.store.set((state) => ({ ...state, sending, status: null, failed: null }));
    this.stopPolling?.();
    const every =
      this.deps.every ??
      ((task, ms) => {
        const timer = setInterval(task, ms);
        return () => clearInterval(timer);
      });
    this.stopPolling = every(() => void this.poll(sending.id), POLL_MS);
  }

  private async running(id: string) {
    try {
      return (await this.deps.request<AgentActive>('agent.active'))?.request_id === id;
    } catch {
      return false;
    }
  }

  // What the turn is doing now. A status that does not come is only a draft missed: the next one tells.
  async poll(id: string) {
    if (this.polling || this.state.sending?.id !== id) return;
    this.polling = true;
    let status: AgentStatus;
    try {
      status = await this.deps.request<AgentStatus>('agent.status', { request_id: id });
    } catch {
      return;
    } finally {
      this.polling = false;
    }
    if (this.state.sending?.id !== id) return;
    // A turn followed without its request (found after a reload) has ended once the backend carries it out no more.
    if (!status.active && !this.awaited.has(id)) {
      this.end(id, null);
      return;
    }
    this.store.set((state) => ({ ...state, status }));
    const question = status.interaction?.interaction_id ?? '';
    if (question && question !== this.told && !this.deps.shown()) {
      this.told = question;
      this.deps.prompt(WAITING, true);
    }
  }

  private finish(id: string, change: Partial<AgentTurnState>) {
    if (this.state.sending?.id !== id) return false;
    this.stopPolling?.();
    this.stopPolling = null;
    this.store.set((state) => ({ ...state, sending: null, status: null, ...change }));
    return true;
  }

  private end(id: string, sent: AgentSend | null) {
    const ended = { n: this.state.ended.n + 1, sent };
    if (this.finish(id, { ended }) && !this.deps.shown()) this.deps.prompt(ENDED, false);
  }

  private fail(id: string, text: string, error: unknown) {
    this.finish(id, { failed: text });
    this.deps.notify(words(error), true);
  }
}
