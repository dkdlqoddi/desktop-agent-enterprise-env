import { useEffect, type RefObject } from 'react';
import { useSession } from './state';

// The last request for the chat's message box that was met (Session.chatFocus counts them).
let met = 0;

// Puts the caret in the chat's message box each time the quick-access shortcut or the tray's 채팅 열기 asks for it
// (app.tsx, chat:focus): once per request, also the one that brought the chat up, and not again on a later visit. The
// caret only goes there: nothing is typed or sent. While a turn runs the box is disabled and takes no caret, so the
// chat is only shown.
export function useChatFocus(box: RefObject<HTMLTextAreaElement | null>) {
  const asked = useSession((session) => session.chatFocus);
  useEffect(() => {
    if (asked <= met) return;
    met = asked;
    box.current?.focus();
  }, [asked, box]);
}
