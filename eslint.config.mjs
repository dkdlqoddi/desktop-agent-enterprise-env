import js from '@eslint/js';
import nextPlugin from '@next/eslint-plugin-next';
import { defineConfig, globalIgnores } from 'eslint/config';
// eslint-plugin-jsx-a11y 6.10.2 still names ESLint 9 as its peer, though its rules use nothing ESLint 10 removed
// (context.options, context.report, context.settings): package.json's overrides lets npm install it beside ESLint 10.
// Drop that override once a release names ESLint 10.
import jsxA11y from 'eslint-plugin-jsx-a11y';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';
import tseslint from 'typescript-eslint';

// npm run lint (npm run lint:fix fixes what it can) checks the screens (src: TypeScript, React, Next.js), the Electron
// main process with its preloads (electron: CommonJS), the build scripts (scripts: ES modules) and the tests; the Python
// backend has ruff (npm run lint:python). Prettier formats every file, so no rule here is about layout. A rule that does
// not fit this code is turned off below with the reason, for every file or for the files it does not fit; a hook's
// dependency list that leaves a value out on purpose says why in its eslint-disable-next-line comment.

// The destructuring that leaves a property out of the rest (`const { password: _password, ...rest } = site`).
const unusedVariables = ['error', { ignoreRestSiblings: true }];

export default defineConfig(
  globalIgnores([
    '.next/',
    'out/',
    'dist/',
    'release/',
    'build/',
    'test-results/',
    'playwright-report/',
    // The agent worktrees of this checkout and the development app's own data: whole copies of the repository that
    // the type-aware rules would read as well, until the memory runs out.
    '.claude/',
    '.local-data/',
    'backend/',
    'next-env.d.ts',
  ]),
  js.configs.recommended,
  {
    // A disable comment that no longer silences anything fails the run, so none outlives its reason.
    linterOptions: { reportUnusedDisableDirectives: 'error' },
    rules: { 'no-unused-vars': unusedVariables },
  },

  // TypeScript, with the types of tsconfig.json (the screens, the E2E tests and the root config files).
  {
    files: ['**/*.{ts,tsx}'],
    extends: [tseslint.configs.recommendedTypeChecked],
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    rules: {
      '@typescript-eslint/no-unused-vars': unusedVariables,
      // The `any` these report comes from where TypeScript gives no type: a promise's rejection (`.catch((error) =>
      // notify(error.message))`), JSON from Playwright and the backend. strict (noImplicitAny) already keeps `any` out
      // of what the code declares itself.
      '@typescript-eslint/no-unsafe-argument': 'off',
      '@typescript-eslint/no-unsafe-assignment': 'off',
      '@typescript-eslint/no-unsafe-call': 'off',
      '@typescript-eslint/no-unsafe-member-access': 'off',
      '@typescript-eslint/no-unsafe-return': 'off',
      // React ignores what an event handler returns: an async onClick or onSubmit is fine as long as it handles its
      // own errors, which the screens' handlers do. Promises handed to anything else are still checked.
      '@typescript-eslint/no-misused-promises': [
        'error',
        { checksVoidReturn: { attributes: false } },
      ],
    },
  },

  // The screens: the rules of hooks, accessibility and Next.js.
  {
    files: ['src/**/*.{ts,tsx}'],
    extends: [
      reactHooks.configs.flat['recommended-latest'],
      jsxA11y.flatConfigs.recommended,
      nextPlugin.configs['core-web-vitals'],
    ],
    rules: {
      'react-hooks/exhaustive-deps': 'error',
      // These four of the React Compiler's rules flag code that would keep the compiler from memoizing it. The screens
      // are not compiled with it (next.config.ts), and what they flag is correct React used on purpose: a callback's
      // latest version kept in a ref during render (modal, toast, reader), layouts cached in a ref (graph), DOM and ref
      // writes in callbacks and effects (windowing, app), effects that load data and reset their state first, and the
      // current time read during render (tasks). The compiler's other rules stay on.
      'react-hooks/set-state-in-effect': 'off',
      'react-hooks/refs': 'off',
      'react-hooks/immutability': 'off',
      'react-hooks/purity': 'off',
      // autoFocus is used where focus has to move when something opens: to the safe answer of a confirmation (취소,
      // 계속 쓰기), the title of the document being written and the error page's 다시 시도. The rule refuses it
      // everywhere.
      'jsx-a11y/no-autofocus': 'off',
      // The settings' labels keep their text one level deeper (label > span > strong), and SourceField renders the
      // <input> its label names.
      'jsx-a11y/label-has-associated-control': [
        'error',
        { depth: 3, controlComponents: ['SourceField'] },
      ],
      // Code and tables that scroll sideways take the focus, so a keyboard can scroll them (axe:
      // scrollable-region-focusable, which tests/a11y.spec.ts runs).
      'jsx-a11y/no-noninteractive-tabindex': [
        'error',
        { tags: ['pre', 'table'], roles: ['tabpanel'], allowExpressionValues: true },
      ],
      // The recommended options, and <nav role="tablist"> (the admin page's sections), which ARIA in HTML allows.
      'jsx-a11y/no-noninteractive-element-to-interactive-role': [
        'error',
        {
          ul: ['listbox', 'menu', 'menubar', 'radiogroup', 'tablist', 'tree', 'treegrid'],
          ol: ['listbox', 'menu', 'menubar', 'radiogroup', 'tablist', 'tree', 'treegrid'],
          li: ['menuitem', 'menuitemradio', 'menuitemcheckbox', 'option', 'row', 'tab', 'treeitem'],
          table: ['grid'],
          td: ['gridcell'],
          fieldset: ['radiogroup', 'presentation'],
          nav: ['tablist'],
        },
      ],
    },
  },
  {
    // Mouse shortcuts beside a control the keyboard reaches: a click on a dialog's backdrop closes it as Esc and its
    // close button do, a click on a notice closes it as its close button does. The document list handles the arrow
    // keys its rows' buttons send (event delegation); the list itself takes no focus.
    files: [
      'src/components/modal.tsx',
      'src/components/toast.tsx',
      'src/components/widgets/documents.tsx',
    ],
    rules: {
      'jsx-a11y/click-events-have-key-events': 'off',
      'jsx-a11y/no-noninteractive-element-interactions': 'off',
    },
  },
  {
    // These turn whatever they are given (a thrown value, a form field's value) into text on purpose.
    files: ['src/components/error-boundary.tsx', 'src/components/routine-form.tsx'],
    rules: { '@typescript-eslint/no-base-to-string': 'off' },
  },

  // The main process, its preloads and the main-process tests: CommonJS on Node.js.
  {
    files: ['electron/**/*.cjs', 'tests/**/*.cjs'],
    languageOptions: { sourceType: 'commonjs', globals: globals.node },
  },
  // The build scripts and this file: ES modules on Node.js.
  {
    files: ['scripts/**/*.mjs', '*.mjs'],
    languageOptions: { sourceType: 'module', globals: globals.node },
  },
  // What tsconfig.electron.json type-checks is also checked for promises left unhandled: an unhandled rejection in the
  // main process is lost, and an async listener whose promise nobody reads hides its failure.
  {
    files: ['electron/**/*.cjs', 'scripts/**/*.mjs'],
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: {
        project: './tsconfig.electron.json',
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: { '@typescript-eslint': tseslint.plugin },
    rules: {
      '@typescript-eslint/await-thenable': 'error',
      '@typescript-eslint/no-floating-promises': 'error',
      '@typescript-eslint/no-misused-promises': 'error',
    },
  },
  {
    // CommonJS for this parser too: a file's top level is its module's scope, not the global one.
    files: ['electron/**/*.cjs'],
    languageOptions: { parserOptions: { ecmaFeatures: { globalReturn: true } } },
  },
  // The recorder's page: a classic script in a sandboxed page, with the bridge recorder-preload.cjs exposes.
  {
    files: ['electron/recorder/page.js'],
    languageOptions: {
      sourceType: 'script',
      globals: { ...globals.browser, recorder: 'readonly' },
    },
  },
  {
    // ANSI colour codes in a terminal's output, the control characters an address or a decrypted text must not hold,
    // and those browsers ignore in a URL (the sanitizer's test), are matched on purpose.
    files: [
      'electron/demo-plan.cjs',
      'electron/sites.cjs',
      'scripts/dev.mjs',
      'tests/sanitize.test.cjs',
    ],
    rules: { 'no-control-regex': 'off' },
  },

  // The E2E tests (Playwright).
  {
    files: ['tests/**/*.ts'],
    rules: {
      // Stand-ins for Electron's async APIs (dialog.showOpenDialog) are async functions, so that they return a promise
      // and reject rather than throw, as the real ones do.
      '@typescript-eslint/require-await': 'off',
      // A test breaks the screen on purpose by replacing a prototype's method in the page, keeping the original to call
      // with .call().
      '@typescript-eslint/unbound-method': 'off',
    },
  },
);
