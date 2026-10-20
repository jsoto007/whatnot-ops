import js from '@eslint/js'
import globals from 'globals'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist', 'dist-test', 'playwright-report', 'test-results', 'e2e/fixture/**'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.ts'],
    languageOptions: { ecmaVersion: 2022, globals: { ...globals.browser, ...globals.webextensions } },
    rules: { '@typescript-eslint/no-unused-vars': ['error', { ignoreRestSiblings: true, argsIgnorePattern: '^_' }] },
  },
  {
    files: ['build.mjs', 'e2e/**/*.ts'],
    languageOptions: { globals: { ...globals.node } },
  },
)
