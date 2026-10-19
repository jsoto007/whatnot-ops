import { defineConfig } from '@playwright/test'

// Runs against a production build served by Flask on :5055 (see `make e2e`).
// Set E2E_BASE_URL to point elsewhere.
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5055',
    trace: 'retain-on-failure',
    channel: process.env.PW_CHANNEL === 'chromium' ? undefined : 'chrome',
  },
  reporter: process.env.CI ? 'github' : 'list',
})
