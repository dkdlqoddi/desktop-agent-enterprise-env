'use client';
import { catchError, type ErrorInfo } from 'next/error';
import { AlertCircle } from 'lucide-react';

// The error as text, whatever was thrown: the notice of a failure must not fail itself.
function describe(error: unknown) {
  try {
    return error instanceof Error ? error.message : String(error ?? '');
  } catch {
    return '';
  }
}

// What a part of the screen shows in its place when drawing it failed: the rest of the app, the writing in the
// document window too, stays as it was, and 다시 시도 draws the part again.
export function RenderError({
  message,
  error,
  onRetry,
}: {
  message: string;
  error: unknown;
  onRetry: () => void;
}) {
  const detail = describe(error);
  return (
    <div className="inline-error render-error" role="alert">
      <AlertCircle size={15} aria-hidden="true" />
      <span>
        {message}
        {detail && <small>{detail}</small>}
      </span>
      <button type="button" className="text-button" onClick={onRetry}>
        다시 시도
      </button>
    </div>
  );
}

// A widget, a page or a panel drawn on its own: an error in it shows here, under the part's heading when it has one,
// instead of ending the whole screen.
export default catchError(function Failed(
  { message, heading }: { message: string; heading?: React.ReactNode },
  { error, reset }: ErrorInfo,
) {
  return (
    <>
      {heading}
      <RenderError message={message} error={error} onRetry={reset} />
    </>
  );
});
