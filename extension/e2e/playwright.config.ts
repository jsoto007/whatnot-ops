import { defineConfig } from '@playwright/test'

const PORT = 4173
export const API_PORT = 5056
export const E2E_DEVICE_KEY = 'wn_e2e-playwright-device-key'
const FLASK = process.env.FLASK_BIN ?? '.venv/bin/flask'

export default defineConfig({
  testDir: '.',
  testMatch: /.*\.spec\.ts/,
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? 'github' : 'list',
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: 'retain-on-failure' },
  webServer: [
    {
      command: `python3 -m http.server ${PORT} --bind 127.0.0.1 --directory fixture`,
      url: `http://127.0.0.1:${PORT}/chat.html`,
      reuseExistingServer: !process.env.CI,
    },
    {
      // The real API on the TEST database (APP_ENV=test), with a known device key.
      command: `cd ../../api && ${FLASK} --app wsgi.py db upgrade && ${FLASK} --app wsgi.py e2e-seed && ${FLASK} --app wsgi.py run --port ${API_PORT}`,
      url: `http://127.0.0.1:${API_PORT}/api/health`,
      env: { APP_ENV: 'test', E2E_DEVICE_KEY, FLASK_RUN_HOST: '127.0.0.1' },
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
  ],
})
