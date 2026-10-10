import type { Answer, AssistantCancel, AssistantStreamEvent } from '@/lib/api';
import type { Store } from '@/lib/store';

// An answer the screen asked for with stream (assistant.ask), from the question until the request answers: the text so
// far as its parts come (assistant:delta), how the LLM ended it (done), and then the whole answer as its conversation
// saved it (result). An answer that failed leaves nothing behind: the backend saved nothing of it, and the search box
// shows the failure.
export type LiveAnswer = {
  requestId: string;
  // The conversation it continues, or the new one's, which the first event names.
  conversationId: string | null;
  question: string;
  text: string;
  status: 'streaming' | 'completed' | 'cancelled' | 'error';
  // The LLM stopped at its output limit: what came is the start of a longer answer.
  truncated: boolean;
  error: string;
  // 중지 was pressed and the backend has not stopped the answer yet.
  stopping: boolean;
  result: Answer | null;
  startedAt: number;
};
export type AnswersState = Readonly<Record<string, LiveAnswer>>;

// Whether the request is still waiting for its answer (it streams, or the LLM ended and the answer is being saved).
export const pending = (answer: LiveAnswer) => !answer.result;

// How long an answer that ended stays after its request: the lists of conversations load again meanwhile, and until
// they have it a new conversation is shown from here.
export const KEEP_MS = 5 * 60_000;

// An ID of the screen's own for a question, which the backend names in the answer's events and assistant.cancel takes
// before the conversation's ID is known (backend/agent/assistant.py REQUEST_ID: letters, digits and . _ : -).
export function newRequestId() {
  const random =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
  return `screen:${random}`;
}

// The answers the screen is waiting for, shared by every component that shows one: the search box's answer column
// draws the text as it comes, the lists of conversations show which conversations are still answering. One listener
// takes the main process's events for all of them; an event of a request the screen did not ask for (an AI tool's) is
// left alone.
export class AnswerStream {
  constructor(
    readonly store: Store<AnswersState>,
    private cancel: (requestId: string) => Promise<AssistantCancel>,
    private now: () => number = Date.now,
  ) {}

  // Listens to the main process's events of streamed answers; returns how to stop.
  listen() {
    const on = typeof window === 'undefined' ? undefined : window.privateAgent?.onAssistantDelta;
    return on?.((event) => this.event(event)) ?? (() => {});
  }

  get(requestId: string): LiveAnswer | null {
    return this.store.get()[requestId] ?? null;
  }

  // The newest answer of a conversation, still being written or saved a moment ago.
  latest(conversationId: string): LiveAnswer | null {
    let found: LiveAnswer | null = null;
    for (const answer of Object.values(this.store.get()))
      if (
        answer.conversationId === conversationId &&
        (!found || answer.startedAt > found.startedAt)
      )
        found = answer;
    return found;
  }

  // A question was sent. Answers that ended long ago go now, so the slice keeps only what the screens may still show.
  start(requestId: string, question: string, conversationId: string | null) {
    const at = this.now();
    this.store.set((state) => {
      const kept: Record<string, LiveAnswer> = {};
      for (const [id, answer] of Object.entries(state))
        if (pending(answer) || at - answer.startedAt < KEEP_MS) kept[id] = answer;
      kept[requestId] = {
        requestId,
        conversationId,
        question,
        text: '',
        status: 'streaming',
        truncated: false,
        error: '',
        stopping: false,
        result: null,
        startedAt: at,
      };
      return kept;
    });
  }

  // A part of an answer (delta: added to what came, or all of it again with reset) or its end (done).
  event(event: AssistantStreamEvent) {
    this.change(event.request_id, (answer) => {
      const conversationId = event.conversation_id || answer.conversationId;
      if (event.type === 'delta')
        return {
          ...answer,
          conversationId,
          text: event.reset ? event.text : answer.text + event.text,
        };
      return {
        ...answer,
        conversationId,
        status: event.status,
        truncated: event.truncated,
        error: event.error ?? '',
        stopping: false,
      };
    });
  }

  // The request answered: the answer as its conversation saved it. Its end is taken from it too, in case the event that
  // tells it did not come (the window was loaded again meanwhile).
  finish(requestId: string, result: Answer) {
    this.change(requestId, (answer) => ({
      ...answer,
      conversationId: result.conversation_id,
      status: result.cancelled
        ? 'cancelled'
        : answer.status === 'streaming'
          ? 'completed'
          : answer.status,
      truncated: !!result.truncated || answer.truncated,
      stopping: false,
      result,
    }));
  }

  // The request failed: nothing of it was saved.
  fail(requestId: string) {
    this.remove((answer) => answer.requestId === requestId);
  }

  // The conversation was deleted: its answers go with it.
  forget(conversationId: string) {
    this.remove((answer) => answer.conversationId === conversationId);
  }

  // 중지: the backend stops the answer and saves what came of it, which its end then brings. Stopping an answer that
  // has just ended does nothing.
  async stop(requestId: string) {
    const answer = this.get(requestId);
    if (!answer || !pending(answer) || answer.stopping) return;
    this.change(requestId, (current) => ({ ...current, stopping: true }));
    try {
      const { cancelled } = await this.cancel(requestId);
      if (!cancelled) this.change(requestId, (current) => ({ ...current, stopping: false }));
    } catch (error) {
      this.change(requestId, (current) => ({ ...current, stopping: false }));
      throw error;
    }
  }

  private change(requestId: string, next: (answer: LiveAnswer) => LiveAnswer) {
    this.store.set((state) => {
      const answer = state[requestId];
      return answer ? { ...state, [requestId]: next(answer) } : state;
    });
  }

  private remove(match: (answer: LiveAnswer) => boolean) {
    this.store.set((state) => {
      const kept = Object.fromEntries(Object.entries(state).filter(([, answer]) => !match(answer)));
      return Object.keys(kept).length === Object.keys(state).length ? state : kept;
    });
  }
}
