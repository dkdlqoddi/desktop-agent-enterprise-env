import { useEffect, useId, useImperativeHandle, useLayoutEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';

// Open dialogs, topmost last. showModal() makes everything outside the topmost dialog inert,
// so page-level layers such as the toast render inside it to stay clickable.
let dialogs: HTMLDialogElement[] = [];
const listeners = new Set<() => void>();
function setDialogs(next: HTMLDialogElement[]) {
  dialogs = next;
  listeners.forEach((listener) => listener());
}
export function subscribeModals(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
export const topModal = () => dialogs.at(-1) ?? null;

// Closing from inside the panel as Escape and 닫기 do, which asks first about unsaved work. `then` is what closing
// does instead of closing the panel, such as the editor's 취소 going back to the document.
export type ModalHandle = { requestClose: (then?: () => void) => void };

// The app's one kind of window: a panel sliding in from the right edge (documents, run results).
export default function Modal({
  title,
  subtitle,
  actions,
  children,
  onClose,
  locked = false,
  confirmClose,
  ref,
}: {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
  onClose: () => void;
  locked?: boolean;
  // Unsaved work in the panel: closing asks this in place first.
  confirmClose?: string;
  ref?: React.Ref<ModalHandle>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [asking, setAsking] = useState(false);
  // What 버리고 닫기 does when the question came with `then`; closing the panel otherwise.
  const discard = useRef<(() => void) | null>(null);
  // The field last written in: 계속 쓰기 takes the keyboard back there.
  const writing = useRef<HTMLElement | null>(null);
  const answered = useRef(false);
  // Where a press began: a text selection dragged out of the panel ends on the backdrop, which is no request to close.
  const pressed = useRef<EventTarget | null>(null);
  useEffect(() => {
    if (!confirmClose) setAsking(false);
  }, [confirmClose]);
  const ask = (then?: () => void) => {
    discard.current = then ?? null;
    setAsking(true);
  };
  const requestClose = (then?: () => void) => {
    if (locked) return;
    if (confirmClose) ask(then);
    else (then ?? onClose)();
  };
  useImperativeHandle(ref, () => ({ requestClose }));
  // Escape while the panel asks goes back to the writing.
  const escape = useRef(() => {});
  escape.current = () => (asking ? setAsking(false) : requestClose());
  useLayoutEffect(() => {
    if (asking) {
      answered.current = true;
      return;
    }
    if (!answered.current) return;
    answered.current = false;
    // The question's buttons went with it, and the keyboard with them.
    const field = writing.current;
    if (field?.isConnected && !dialog.current?.contains(document.activeElement)) field.focus();
  }, [asking]);
  // A layout effect: the dialog closes before React removes it, so the browser gives the keyboard back to whatever
  // opened it, as it does for every modal dialog.
  useLayoutEffect(() => {
    const element = dialog.current;
    if (!element) return;
    element.showModal();
    setDialogs([...dialogs, element]);
    // Chromium lets a page refuse only a few close requests in a row, then closes the dialog itself while React keeps
    // it. Escape is taken before it becomes one, wherever the keyboard is in the panel, so it only ever asks.
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented || event.isComposing) return;
      if (topModal() !== element) return;
      event.preventDefault();
      escape.current();
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      element.close();
      setDialogs(dialogs.filter((open) => open !== element));
    };
  }, []);
  return (
    <dialog
      ref={dialog}
      className="drawer"
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault();
        escape.current();
      }}
      onClose={() => {
        // Closed by the browser after all (a close request it no longer let the page refuse): open again while the
        // panel has work to keep, or closed as asked, so the screen and React never disagree. A dialog being removed
        // with its panel, or already open again (React's development checks), is left alone.
        const element = dialog.current;
        if (!element || element.open) return;
        if (!locked && !confirmClose) return onClose();
        element.showModal();
        // A notice shown in the panel comes back above it.
        element.querySelectorAll<HTMLElement>(':popover-open').forEach((layer) => {
          layer.hidePopover();
          layer.showPopover();
        });
        if (confirmClose) ask();
      }}
      onFocus={(e) => {
        if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement)
          writing.current = e.target;
      }}
      onPointerDown={(e) => {
        pressed.current = e.target;
      }}
      onClick={(e) => {
        // A click on the backdrop (the dialog element itself, outside the panel) closes it.
        if (e.target === e.currentTarget && pressed.current === e.currentTarget) requestClose();
      }}
    >
      <div className="drawer-panel">
        <header className="drawer-head">
          <div>
            <h2 id={titleId}>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          {actions && <div className="drawer-actions">{actions}</div>}
          <button
            className="icon-button"
            aria-label="닫기"
            disabled={locked}
            onClick={() => requestClose()}
          >
            <X size={18} />
          </button>
        </header>
        {asking && confirmClose && (
          <div className="confirm drawer-confirm" role="group" aria-label={confirmClose}>
            <span>{confirmClose}</span>
            <button
              type="button"
              className="button danger small"
              onClick={() => {
                const then = discard.current;
                discard.current = null;
                answered.current = false;
                setAsking(false);
                (then ?? onClose)();
              }}
            >
              버리고 닫기
            </button>
            <button
              type="button"
              className="button secondary small"
              autoFocus
              onClick={() => setAsking(false)}
            >
              계속 쓰기
            </button>
          </div>
        )}
        {children}
      </div>
    </dialog>
  );
}
