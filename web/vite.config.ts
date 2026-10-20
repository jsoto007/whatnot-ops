import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Dev: Vite serves the SPA and proxies /api to Flask (:5000).
// Prod: Flask serves web/dist itself (see api/app/spa.py), so no CORS anywhere.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': 'http://localhost:5000' },
  },
  build: { outDir: 'dist', sourcemap: true },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
