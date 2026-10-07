import { useLayoutEffect, useRef, useState } from 'react';

// A destructive action that asks in place instead of opening a dialog: the button turns into the
// question with the same action and 취소.
export default function Confirm({
  label,
  question,
  onConfirm,
  icon,
  disabled = false,
  iconOnly = false,
  name,
}: {
  label: string;
  question: string;
  onConfirm: () => Promise<void> | void;
  icon?: React.ReactNode;
  disabled?: boolean;
  // A list row shows only the icon; `name` then labels it, e.g. "회의록 삭제".
  iconOnly?: boolean;
  name?: string;
}) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const group = useRef<HTMLSpanElement>(null);
  // The question gives the keyboard back to the button it came from, which takes its place again, unless the keyboard
  // went elsewhere meanwhile; a button disabled while the action ran has already dropped it.
  const refocus = useRef(false);
  const answer = () => {
    const focused = document.activeElement;
    refocus.current = !focused || focused === document.body || !!group.current?.contains(focused);
    setAsking(false);
  };
  useLayoutEffect(() => {
    if (asking || !refocus.current) return;
    refocus.current = false;
    if (!button.current?.disabled) button.current?.focus();
  }, [asking]);
  if (!asking)
    return (
      <button
        ref={button}
        type="button"
        className={iconOnly ? 'icon-button' : 'button secondary small'}
        aria-label={name}
        title={iconOnly ? name : undefined}
        disabled={disabled}
        onClick={() => setAsking(true)}
      >
        {icon}
        {!iconOnly && label}
      </button>
    );
  return (
    <span ref={group} className="confirm" role="group" aria-label={question}>
      <span>{question}</span>
      {/* What makes the button unavailable keeps the action from running while it asks, too. */}
      <button
        type="button"
        className="button danger small"
        disabled={busy || disabled}
        autoFocus
        onClick={async () => {
          setBusy(true);
          try {
            await onConfirm();
          } finally {
            setBusy(false);
            answer();
          }
        }}
      >
        {label}
      </button>
      <button type="button" className="button secondary small" disabled={busy} onClick={answer}>
        취소
      </button>
    </span>
  );
}
