import { previewRead } from '../preview';
import type { Method } from './methods.generated';
import type { AssistantStreamEvent, JobProgressEvent, RoutineEvent, ScheduleEvent } from './types';

// The narrow bridge electron/preload.cjs exposes: one request channel, checked by the main process against its ALLOWED
// methods, and the events the main process sends the window. A plain browser (the screens' preview) has none.
declare global {
  interface Window {
    privateAgent?: {
      onAssistantOpen?: (callback: () => void) => () => void;
      // An answer as the LLM writes it, for a question asked with stream (assistant.ask).
      onAssistantDelta?: (callback: (event: AssistantStreamEvent) => void) => () => void;
      onScheduleEvent?: (callback: (event: ScheduleEvent) => void) => () => void;
      onRoutineEvent?: (callback: (event: RoutineEvent) => void) => () => void;
      onImportNotice?: (callback: (text: string) => void) => () => void;
      // A background job (an import, the AI analysis) or an automatic routine changed while the main process followed
      // it (electron/job-progress.cjs).
      onJobProgress?: (callback: (event: JobProgressEvent) => void) => () => void;
      onControl?: (
        handler: (type: string, params: Record<string, unknown>) => Promise<unknown>,
      ) => () => void;
      onApiChange?: (callback: () => void) => () => void;
      request: (
        method: string,
        params?: Record<string, unknown>,
      ) => Promise<{ ok: boolean; result?: unknown; error?: string }>;
    };
  }
}

// One request of the screens. `method` is one the main process knows (methods.generated.ts); tests/methods.test.cjs
// checks that every method the screens call is also in its ALLOWED set and handled there or by the backend.
export async function api<T>(method: Method, params: Record<string, unknown> = {}): Promise<T> {
  if (!window.privateAgent) return previewRead(method) as T;
  const response = await window.privateAgent.request(method, params);
  if (!response.ok) throw new Error(response.error || '요청을 처리하지 못했습니다.');
  return response.result as T;
}
