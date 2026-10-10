// The screen's theme: Windows' own (light or dark), or always light or always dark. It belongs to this PC rather than
// to a Wiki folder, and must be known before the first paint, long before the backend answers; so it is kept in the
// window's local storage, not in the Wiki's preferences. theme.css draws `data-theme` on <html>: absent follows the
// system, "light" and "dark" fix it.
export type Theme = 'system' | 'light' | 'dark';
export const THEMES: [Theme, string][] = [
  ['system', '시스템 설정'],
  ['light', '밝게'],
  ['dark', '어둡게'],
];
const KEY = 'private-agent:theme';

// Run inline in <head> (app/layout.tsx) before anything is drawn, so a dark screen never flashes light. The window
// allows it by its hash (electron/main.cjs); storage the window cannot read leaves the system's theme.
export const THEME_SCRIPT = `try{var t=localStorage.getItem('${KEY}');if(t==='light'||t==='dark')document.documentElement.dataset.theme=t}catch(e){}`;

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
}
