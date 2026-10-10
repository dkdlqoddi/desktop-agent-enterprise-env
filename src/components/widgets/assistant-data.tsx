import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { api, type Briefing, type Task } from '@/lib/api';
import { keepEqual } from '@/lib/query';
import { useInvalidated } from '../state';
import { Loading } from './parts';

type Data = {
  tasks: Task[];
  briefing: Briefing | null;
  notification: { supported: boolean; error: string } | null;
  // Whether tasks and the briefing have loaded once; until then an empty list says nothing.
  loaded: boolean;
  // Why the last load failed, until one succeeds.
  error: string;
  refresh: () => Promise<void>;
};
const Context = createContext<Data | null>(null);
export const useAssistantData = () => useContext(Context)!;

// Tasks, today's briefing and reminder support, shared by the briefing, reminder and task widgets.
// They load again every 30 seconds, when the window gets focus, and after a change of the tasks or the documents (whose
// titles they show).
export function AssistantData({
  children,
  enabled = true,
}: {
  children: React.ReactNode;
  enabled?: boolean;
}) {
  const version = useInvalidated(['tasks', 'documents']);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  const [notification, setNotification] = useState<Data['notification']>(null);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const refresh = useCallback(async () => {
    const current = ++generation.current;
    try {
      const [items, snapshot, status] = await Promise.all([
        api<Task[]>('tasks.list'),
        api<Briefing>('briefing.get', { timezone_offset: new Date().getTimezoneOffset() }),
        api<{ supported: boolean; error: string }>('reminders.status'),
      ]);
      if (current !== generation.current) return;
      // What did not change keeps its objects, so the widgets that show it do not draw again.
      setTasks((shown) => keepEqual(shown, items));
      setBriefing((shown) => keepEqual(shown, snapshot));
      setNotification((shown) => keepEqual(shown, status));
      setError('');
    } catch (e) {
      // Only the latest load answers for what the widgets show.
      if (current === generation.current) setError((e as Error).message);
      throw e;
    }
  }, []);
  useEffect(() => {
    if (!enabled) return;
    const load = () => void refresh().catch(() => {});
    load();
    const timer = setInterval(load, 30000);
    window.addEventListener('focus', load);
    return () => {
      generation.current++;
      clearInterval(timer);
      window.removeEventListener('focus', load);
    };
  }, [refresh, version, enabled]);
  // The same value until something in it changes: the widgets that read it draw again only then.
  const value = useMemo(
    () => ({ tasks, briefing, notification, loaded: !!briefing, error, refresh }),
    [tasks, briefing, notification, error, refresh],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

// Why the tasks could not be loaded, with a way to try again.
export function LoadError() {
  const { error, refresh } = useAssistantData();
  if (!error) return null;
  return (
    <p className="inline-error" role="alert">
      {error}
      <button type="button" className="text-button" onClick={() => void refresh().catch(() => {})}>
        다시 시도
      </button>
    </p>
  );
}

// A format's lists once the tasks have loaded: a spinner until then, and a failed load in their place rather than an
// empty list that reads as nothing to do. A later failure shows above what loaded last.
export function Loaded({ children }: { children: React.ReactNode }) {
  const { loaded, error } = useAssistantData();
  return (
    <>
      <LoadError />
      {loaded ? children : !error && <Loading />}
    </>
  );
}
