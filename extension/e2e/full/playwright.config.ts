import { defineConfig } from '@playwright/test'

// The whole laptop process against the real portal: web/dist served by Flask on the TEST database
// (APP_ENV=test), jobs inline, fake AI. `npm run test:full` builds the extension and the web app first.
export const PORT = 5058
const FLASK = process.env.FLASK_BIN ?? '.venv/bin/flask'

export default defineConfig({
  testDir: '.',
  testMatch: /.*\.spec\.ts/,
  timeout: 120_000,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? 'github' : 'list',
  use: { baseURL: `http://127.0.0.1:${PORT}`, channel: 'chromium', trace: 'retain-on-failure' },
  webServer: {
    command: `cd ../../../api && ${FLASK} --app wsgi.py db upgrade && ${FLASK} --app wsgi.py run --port ${PORT} --no-debugger`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    env: { APP_ENV: 'test', RATE_LIMITS: 'off', JOBS_INLINE: '1', AI_PROVIDER: 'fake', FLASK_RUN_HOST: '127.0.0.1' },
    reuseExistingServer: false,
    timeout: 60_000,
  },
})
