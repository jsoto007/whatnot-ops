import { readFileSync } from 'node:fs'
import { defineConfig, type Plugin } from 'vitest/config'
import react from '@vitejs/plugin-react'

const LEGAL_FILES = ['LICENSE.md', 'NOTICE.md', 'THIRD_PARTY_LICENSES.txt']
const legalText = (fileName: string) => readFileSync(new URL(`../${fileName}`, import.meta.url), 'utf8')

/** Puts the licence and the open-source notices beside the bundle, so /LICENSE.md is served with it
 *  (sign-up links to it). The dev server serves them at the same paths. */
const legalFiles = (): Plugin => ({
  name: 'legal-files',
  configureServer(server) {
    server.middlewares.use((req, res, next) => {
      const fileName = (req.url ?? '').split('?')[0]!.slice(1)
      if (!LEGAL_FILES.includes(fileName)) return next()
      res.setHeader('Content-Type', `text/${fileName.endsWith('.md') ? 'markdown' : 'plain'}; charset=utf-8`)
      res.end(legalText(fileName))
    })
  },
  generateBundle() {
    for (const fileName of LEGAL_FILES) this.emitFile({ type: 'asset', fileName, source: legalText(fileName) })
  },
})

// Dev: Vite serves the SPA and proxies /api to Flask (:5050). Ports 5000 (macOS AirPlay),
// 5001/8081 (other projects) and 5173 (other Vite apps) are avoided on purpose.
// Prod: Flask serves web/dist itself (see api/app/spa.py), so no CORS anywhere.
export default defineConfig({
  plugins: [react(), legalFiles()],
  server: {
    port: 5174,
    strictPort: true,
    proxy: { '/api': 'http://localhost:5050' },
  },
  // No public source maps: Flask serves dist/ to anyone (api/app/spa.py also refuses *.map).
  build: {
    outDir: 'dist',
    sourcemap: false,
    // Copyright notice at the top of every script the server sends to a browser
    // (postBanner: added after minification, which would strip a plain banner).
    rolldownOptions: {
      output: {
        postBanner:
          '/*! Sales Ops. Copyright (c) 2026 SotoDev. All rights reserved. Proprietary and confidential; use only under LICENSE.md. */',
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
