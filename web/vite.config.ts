import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Dev: Vite serves the SPA and proxies /api to Flask (:5050). Ports 5000 (macOS AirPlay),
// 5001/8081 (other projects) and 5173 (other Vite apps) are avoided on purpose.
// Prod: Flask serves web/dist itself (see api/app/spa.py), so no CORS anywhere.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    strictPort: true,
    proxy: { '/api': 'http://localhost:5050' },
  },
  // No public source maps: Flask serves dist/ to anyone (api/app/spa.py also refuses *.map).
  build: { outDir: 'dist', sourcemap: false },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
