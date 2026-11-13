// The window's own background, which shows wherever the screen has not drawn yet: in the part a resize uncovers until
// the screen draws it, and before a screen loaded again paints. It follows the theme the screen shows
// (src/components/theme.ts): the screen tells the main process its setting as it loads and whenever the user changes
// it (app.theme), and the setting becomes Electron's theme source. Windows' own light or dark mode then decides
// 'system', and the menus Electron draws follow the setting too. Nothing here needs Electron itself, so
// tests/window-theme.test.cjs runs it under plain Node.js.

// The canvas of src/styles/theme.css (--canvas) in each theme; tests/theme.test.cjs keeps them the same.
const BACKGROUND = Object.freeze({ light: '#f3f5fa', dark: '#0f121a' });
const THEMES = Object.freeze(['system', 'light', 'dark']);

class WindowTheme {
  /**
   * @param {{
   *   nativeTheme: Pick<Electron.NativeTheme, 'shouldUseDarkColors' | 'themeSource' | 'on'>,
   *   windows: () => (Electron.BrowserWindow | null | undefined)[],
   * }} options `windows`: the windows that show the screen, painted again when the theme changes.
   */
  constructor({ nativeTheme, windows }) {
    this.nativeTheme = nativeTheme;
    this.windows = windows;
    // Windows switched between light and dark, or the setting changed.
    nativeTheme.on('updated', () => this.paint());
  }

  // The background for a window opened now.
  get background() {
    return this.nativeTheme.shouldUseDarkColors ? BACKGROUND.dark : BACKGROUND.light;
  }

  /** @param {unknown} theme The screen's setting: 'system', 'light' or 'dark'. */
  set(theme) {
    if (typeof theme !== 'string' || !THEMES.includes(theme))
      throw new Error('화면 테마는 system, light, dark 가운데 하나여야 합니다.');
    this.nativeTheme.themeSource = /** @type {'system' | 'light' | 'dark'} */ (theme);
    // Electron also says so ('updated'), but the window must not wait for it.
    this.paint();
    return { theme };
  }

  paint() {
    const color = this.background;
    for (const window of this.windows())
      if (window && !window.isDestroyed()) window.setBackgroundColor(color);
  }
}

module.exports = { BACKGROUND, THEMES, WindowTheme };
