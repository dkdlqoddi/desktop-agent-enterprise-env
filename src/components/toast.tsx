import { useEffect, useRef, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';
import { Check, CircleAlert, Info, X } from 'lucide-react';
import { subscribeModals, topModal } from './modal';

export default function Toast({
  message,
  error,
  notice = false,
  onClose,
}: {
  message: string;
  error: boolean;
  notice?: boolean;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  // An open modal dialog makes the rest of the page inert, so render inside the topmost one.
  const host = useSyncExternalStore(subscribeModals, topModal, () => null);
  useEffect(() => {
    // Moving between hosts remounts the node; showing it again stacks it above the new host.
    // A dialog removed in the same commit leaves it detached until the next render moves it.
    if (ref.current?.isConnected) ref.current.showPopover();
  }, [message, error, notice, host]);
  const toast = (
    <div
      ref={ref}
      popover="manual"
      className={`toast ${error ? 'error' : notice ? 'notice' : ''}`}
      role={error || notice ? 'alert' : 'status'}
      onClick={() => {
        // Clicking anywhere on the toast closes it, unless its text was just selected to copy.
        if (!window.getSelection()?.toString()) onClose();
      }}
    >
      {error ? <Info size={19} /> : notice ? <CircleAlert size={19} /> : <Check size={19} />}
      <span>{message}</span>
      <button type="button" aria-label="알림 닫기" onClick={onClose}>
        <X size={16} />
      </button>
    </div>
  );
  return host ? createPortal(toast, host) : toast;
}
