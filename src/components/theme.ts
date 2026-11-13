import { api } from '@/lib/api/transport';

// The screen's theme: Windows' own (light or dark), or always light or always dark. It belongs to this PC rather than
// to a Wiki folder, and must be known before the first paint, long before the backend answers; so it is kept in the
// window's local storage, not in the Wiki's preferences. theme.css draws `data-theme` on <html>: absent follows the
// system, "light" and "dark" fix it. The main process is told the setting (app.theme) as the screen loads and when it
// changes: the window's own background, which shows while the window is resized, and Electron's theme follow it
// (electron/window-theme.cjs).
export type Theme = 'system' | 'light' | 'dark';
export const THEMES: [Theme, string][] = [
  ['system', '시스템 설정'],
  ['light', '밝게'],
  ['dark', '어둡게'],
];
const KEY = 'private-agent:theme';

// Run inline in <head> (app/layout.tsx) before anything is drawn, so a dark screen never flashes light and the main
// process hears the setting as early as it can. The window allows it by its hash (electron/protocol.cjs); storage the
// window cannot read leaves the system's theme, and a plain browser (the screens' preview) has no main process to tell.
export const THEME_SCRIPT = `(function(){var t='system';try{var s=localStorage.getItem('${KEY}');if(s==='light'||s==='dark'){t=s;document.documentElement.dataset.theme=s}}catch(e){}var a=window.privateAgent;if(a)a.request('app.theme',{theme:t}).catch(function(){})})()`;

export function storedTheme(): Theme {
  try {
    const value = localStorage.getItem(KEY);
    return value === 'light' || value === 'dark' ? value : 'system';
  } catch {
    return 'system';
  }
}

export function saveTheme(theme: Theme) {
  const root = document.documentElement;
  if (theme === 'system') delete root.dataset.theme;
  else root.dataset.theme = theme;
  try {
    if (theme === 'system') localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, theme);
  } catch {
    // The theme still applies to this window until it closes.
  }
  // Only the window's background is late if this fails; the screen has its theme already.
  void api('app.theme', { theme }).catch(() => {});
}
