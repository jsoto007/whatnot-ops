// Bundles the extension with esbuild. `--test` writes dist-test/ with the
// fixture origin added to the content-script matches so Playwright can load it.
import { build } from 'esbuild'
import { cpSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'

const test = process.argv.includes('--test')
const out = test ? 'dist-test' : 'dist'
// DEFAULT_SERVER_URL bakes the team's server into the build (Settings still shows it and takes a key).
const defaults = { serverUrl: (process.env.DEFAULT_SERVER_URL || '').trim().replace(/\/+$/, '') }

rmSync(out, { recursive: true, force: true })
mkdirSync(out, { recursive: true })

await build({
  entryPoints: {
    content: 'src/content/index.ts',
    background: 'src/background.ts',
    search: 'src/search/index.ts',
    options: 'src/options/index.ts',
  },
  bundle: true,
  format: 'iife',
  target: 'chrome120',
  outdir: out,
  sourcemap: test ? 'inline' : false,
  logLevel: 'info',
})

const manifest = JSON.parse(readFileSync('manifest.json', 'utf8'))
if (test) {
  manifest.name += ' (test build)'
  manifest.content_scripts[0].matches.push('http://127.0.0.1/*', 'http://localhost/*')
}
writeFileSync(`${out}/manifest.json`, JSON.stringify(manifest, null, 2))
for (const f of ['search.html', 'options.html', 'icon16.png', 'icon48.png', 'icon128.png']) cpSync(f, `${out}/${f}`)
writeFileSync(`${out}/defaults.json`, JSON.stringify(defaults))
console.log(`built ${out}/`)
