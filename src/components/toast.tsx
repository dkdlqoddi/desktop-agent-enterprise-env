import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { createPortal } from 'react-dom';
import { Check, CircleAlert, Info, X } from 'lucide-react';
import { subscribeModals, topModal } from './modal';

// A way back from what a message tells, e.g. 되돌리기 after a deletion.
export type ToastAction = { label: string; run: () => Promise<void> | void };
// notice: a caution the user should read, which stays until closed as an error does.
export type ToastMessage = {
  message: string;
  error: boolean;
  notice?: boolean;
  action?: ToastAction;
};
type Item = ToastMessage & { id: number };

// How many messages show at once, stacked up from the bottom edge, and the space between them.
const SHOWN = 3;
const GAP = 8;
// How long a success shows; a message with a way back stays a little longer.
const SUCCESS_MS = 6000;
const ACTION_MS = 10000;

// Errors and notices wait until the user closes them; successes close by themselves.
const sticky = (item: ToastMessage) => item.error || !!item.notice;
// A plain success tells where things are now, so a newer one takes its place.
const plain = (item: ToastMessage) => !sticky(item) && !item.action;
const same = (a: ToastMessage, b: ToastMessage) =>
  a.message === b.message && a.error === b.error && !!a.notice === !!b.notice;

// The messages on screen: the newest success always, and the others in the order they came, up to SHOWN; the rest wait
// their turn. Newest first, as they stack up from the bottom edge.
function shown(items: Item[]) {
  const success = items.find(plain);
  const others = items.filter((item) => item !== success);
  return [...others.slice(0, success ? SHOWN - 1 : SHOWN), ...(success ? [success] : [])].sort(
    (a, b) => b.id - a.id,
  );
}

// The app's message queue. A message shown again is not stacked twice: it moves to the newest place, and a success
// starts its time again. A success's time runs from when it shows, so one that waited its turn is still read.
export function useToasts() {
  const [items, setItems] = useState<Item[]>([]);
  const next = useRef(0);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());
  const push = useCallback((toast: ToastMessage) => {
    // A failure caught as something other than an Error has no message to show.
    if (typeof toast.message !== 'string' || !toast.message.trim()) return;
    const item = { ...toast, id: ++next.current };
    setItems((current) => [
      ...current.filter(
        (other) => other.action || !(same(other, item) || (plain(item) && plain(other))),
      ),
      item,
    ]);
  }, []);
  const close = useCallback(
    (id: number) => setItems((current) => current.filter((item) => item.id !== id)),
    [],
  );
  useEffect(() => {
    for (const item of shown(items)) {
      if (sticky(item) || timers.current.has(item.id)) continue;
      timers.current.set(
        item.id,
        setTimeout(() => close(item.id), item.action ? ACTION_MS : SUCCESS_MS),
      );
    }
    for (const [id, timer] of timers.current)
      if (!items.some((item) => item.id === id)) {
        clearTimeout(timer);
        timers.current.delete(id);
      }
  }, [items, close]);
  useEffect(() => {
    const running = timers.current;
    return () => {
      running.forEach(clearTimeout);
      running.clear();
    };
  }, []);
  return { items, push, close };
}

export default function Toasts({
  items,
  onClose,
}: {
  items: Item[];
  onClose: (id: number) => void;
}) {
  const layers = useRef(new Map<number, HTMLDivElement>());
  // An open modal dialog makes the rest of the page inert, so render inside the topmost one.
  const host = useSyncExternalStore(subscribeModals, topModal, () => null);
  const visible = shown(items);
  const waiting = items.length - visible.length;
  // Each message is a layer of its own above the page: shown once it is in the document, and placed above the ones
  // below it, the first where the stylesheet puts it (lower on a narrow window). Moving between hosts remounts the
  // nodes; showing them again stacks them above the new host. A dialog removed in the same commit leaves them detached
  // until the next render moves them.
  const order = useRef(visible);
  order.current = visible;
  const place = useCallback(() => {
    let bottom: number | null = null;
    for (const item of order.current) {
      const layer = layers.current.get(item.id);
      if (!layer?.isConnected) continue;
      if (!layer.matches(':popover-open')) layer.showPopover();
      if (bottom === null) {
        layer.style.removeProperty('bottom');
        bottom = parseFloat(getComputedStyle(layer).bottom) || 0;
      } else layer.style.bottom = `${bottom}px`;
      bottom += layer.getBoundingClientRect().height + GAP;
    }
  }, []);
  useLayoutEffect(place);
  // A narrower window wraps the messages onto more lines.
  useEffect(() => {
    window.addEventListener('resize', place);
    return () => window.removeEventListener('resize', place);
  }, [place]);
  const toasts = visible.map((item, index) => {
    const alert = sticky(item);
    return (
      <div
        // A newer success takes the place of the one shown in the same layer, whose live region then reads it out.
        key={plain(item) ? 'success' : item.id}
        ref={(layer) => {
          if (layer) layers.current.set(item.id, layer);
          else layers.current.delete(item.id);
        }}
        popover="manual"
        className={`toast ${item.error ? 'error' : item.notice ? 'notice' : ''}`}
        // Errors and notices interrupt; a success is read out once the screen reader is free.
        role={alert ? 'alert' : 'status'}
        aria-live={alert ? undefined : 'polite'}
        onClick={() => {
          // Clicking anywhere on the toast closes it, unless its text was just selected to copy.
          if (!window.getSelection()?.toString()) onClose(item.id);
        }}
      >
        {item.error ? (
          <Info size={19} />
        ) : item.notice ? (
          <CircleAlert size={19} />
        ) : (
          <Check size={19} />
        )}
        <span>{item.message}</span>
        {/* The messages waiting their turn are counted on the top one, the oldest shown. */}
        {waiting > 0 && index === visible.length - 1 && (
          <small style={{ flexShrink: 0, opacity: 0.8 }}>알림 {waiting}개 더</small>
        )}
        {item.action && (
          <button
            type="button"
            className="toast-action"
            onClick={(event) => {
              event.stopPropagation();
              onClose(item.id);
              void item.action!.run();
            }}
          >
            {item.action.label}
          </button>
        )}
        <button
          type="button"
          aria-label="알림 닫기"
          onClick={(event) => {
            event.stopPropagation();
            onClose(item.id);
          }}
        >
          <X size={16} />
        </button>
      </div>
    );
  });
  if (!toasts.length) return null;
  return host ? createPortal(toasts, host) : toasts;
}
