import js from '@eslint/js'
import globals from 'globals'
import tseslint from 'typescript-eslint'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import jsxA11y from 'eslint-plugin-jsx-a11y'
import prettier from 'eslint-config-prettier'

/**
 * Lint policy during the UI revamp.
 *
 * ERROR  — correctness and things that are already clean. These must never regress.
 * WARN   — the accessibility and dead-code debt catalogued in
 *          docs/UI_UX_AUDIT_AND_REVAMP_PLAN.md. Failing the build on these today
 *          would mean a permanently red gate, so instead `npm run lint` pins a
 *          maximum warning count (see package.json). The count only ever goes
 *          down: Phases 2–6 pay the debt off and lower the ceiling.
 */
export default tseslint.config(
  { ignores: ['dist', 'node_modules', 'coverage', 'storybook-static', '*.tsbuildinfo', 'prerender/.out'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser, ...globals.es2021 },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
      'jsx-a11y': jsxA11y,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      ...jsxA11y.flatConfigs.recommended.rules,

      // ── Hard errors: currently clean, must stay clean ──────────────────────
      // Debug logging must not reach production again (Phase 0.3 removed 10 calls).
      'no-console': ['error', { allow: ['error'] }],
      'no-debugger': 'error',

      // A deliberate idiom throughout this codebase: swallow a non-critical
      // failure rather than break the screen. Empty *blocks* are still errors.
      'no-empty': ['error', { allowEmptyCatch: true }],

      // ── Revamp debt: visible, capped, ratcheting down ──────────────────────
      // Native confirm()/alert() on destructive actions — audit findings B3, ST2.
      // Phase 2.8 / 5.4 replace these with a ConfirmDialog that shows what is
      // about to change. 13 call sites today.
      'no-alert': 'warn',
      // Audit finding LG-/R-series: autoFocus steals focus from screen readers.
      // Phase 6.1. 19 call sites today.
      'jsx-a11y/no-autofocus': 'warn',
      '@typescript-eslint/no-unused-expressions': 'warn',

      '@typescript-eslint/no-unused-vars': [
        'warn',
        {
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrors: 'none',
        },
      ],
      '@typescript-eslint/no-explicit-any': 'warn',
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],

      // Accessibility — every one of these is a real finding in the audit.
      'jsx-a11y/no-static-element-interactions': 'warn',
      'jsx-a11y/click-events-have-key-events': 'warn',
      'jsx-a11y/no-noninteractive-element-interactions': 'warn',
      'jsx-a11y/label-has-associated-control': 'warn',
      'jsx-a11y/anchor-is-valid': 'warn',
    },
  },
  // Test files run in Node + jsdom and may reach for test globals.
  {
    files: ['**/*.test.{ts,tsx}', '**/setupTests.ts'],
    languageOptions: { globals: { ...globals.node } },
    rules: { 'no-console': 'off' },
  },
  prettier,
  {
    // The build-time prerenderer. It exports render helpers rather than
    // components, so react-refresh's "only export components" rule has nothing
    // to say about it — this code never runs in a browser and is never
    // hot-reloaded.
    files: ['prerender/**/*.{ts,tsx}'],
    rules: { 'react-refresh/only-export-components': 'off' },
  },
)
