import { useEffect, useId, useRef, useState } from 'react';
import { Check, CircleAlert, Eye, EyeOff, KeyRound } from 'lucide-react';

// A secret typed into a form, such as an API Key or a custom header's value. It is masked until its eye button shows
// it, and the field never holds the saved value, which stays in the main process: `saved` (has_value) shows instead that
// one is in use, and 다시 입력 empties the field and puts the keyboard in it for a new one. `stale` marks a saved value
// kept for another server than the one now typed, which saving drops unless it is typed again. The wrapper's class
// keeps what is typed out of what AI tools read of the screen and out of their captures (app-control.ts secret).
export default function SecretInput({
  label,
  name,
  value,
  placeholder,
  saved = false,
  stale = false,
  onChange,
}: {
  // What the buttons act on, as they say it: "API Key 표시", "API Key 다시 입력".
  label: string;
  // The field's own accessible name, where no <label> around it gives one.
  name?: string;
  value: string;
  placeholder: string;
  saved?: boolean;
  stale?: boolean;
  onChange: (value: string) => void;
}) {
  const [shown, setShown] = useState(false);
  const field = useRef<HTMLInputElement>(null);
  const state = useId();
  // The saved value is used while nothing new is typed.
  const inUse = saved && !value;
  // Once a value is saved, the next one typed starts hidden again.
  useEffect(() => {
    if (inUse) setShown(false);
  }, [inUse]);
  return (
    <span className="secret-input">
      <KeyRound size={15} aria-hidden="true" />
      <input
        ref={field}
        type={shown ? 'text' : 'password'}
        aria-label={name}
        aria-describedby={inUse ? state : undefined}
        autoComplete="new-password"
        spellCheck={false}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        // Narrower than its own default width beside the state and the buttons, as in a header's row.
        style={{ minWidth: 0 }}
      />
      {inUse && (
        <small
          id={state}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 3,
            flexShrink: 0,
            fontSize: 12,
            whiteSpace: 'nowrap',
            color: stale ? 'var(--warn)' : 'var(--ok)',
          }}
        >
          {stale ? <CircleAlert size={13} /> : <Check size={13} />}
          {stale ? '다시 입력 필요' : '등록됨'}
        </small>
      )}
      {saved && (
        <button
          type="button"
          className="text-button"
          aria-label={`${label} 다시 입력`}
          style={{ flexShrink: 0, fontSize: 12, whiteSpace: 'nowrap' }}
          onClick={() => {
            onChange('');
            setShown(false);
            field.current?.focus();
          }}
        >
          다시 입력
        </button>
      )}
      <button
        type="button"
        className="icon-button"
        aria-label={shown ? `${label} 숨기기` : `${label} 표시`}
        onClick={() => setShown(!shown)}
      >
        {shown ? <EyeOff size={15} /> : <Eye size={15} />}
      </button>
    </span>
  );
}
