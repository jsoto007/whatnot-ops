import { defineConfig } from '@playwright/test'

const PORT = 4173

export default defineConfig({
  testDir: '.',
  testMatch: /.*\.spec\.ts/,
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? 'github' : 'list',
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: 'retain-on-failure' },
  webServer: {
    command: `python3 -m http.server ${PORT} --bind 127.0.0.1 --directory fixture`,
    url: `http://127.0.0.1:${PORT}/chat.html`,
    reuseExistingServer: !process.env.CI,
  },
})
