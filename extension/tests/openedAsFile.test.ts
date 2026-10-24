import { readFileSync } from 'node:fs'
import path from 'node:path'

const body = (page: string) => {
  const html = readFileSync(path.resolve(__dirname, '..', page), 'utf8')
  return html.slice(html.indexOf('<header>'), html.indexOf('<script'))
}

const PAGES = [
  { name: 'Settings', html: 'options.html', module: '../src/options/index', gone: '#pair' },
  { name: 'Chat Search', html: 'search.html', module: '../src/search/index', gone: '#show' },
]

describe.each(PAGES)('$name opened as a file (no extension APIs)', ({ name, html, module, gone }) => {
  beforeEach(() => {
    vi.resetModules()
    document.body.innerHTML = body(html)
    // Chrome defines window.chrome on ordinary pages too, just without storage/runtime.
    vi.stubGlobal('chrome', {})
  })
  afterEach(() => vi.unstubAllGlobals())

  it('says how to open it from Chrome instead of failing', async () => {
    const rejections: unknown[] = []
    const onRejection = (e: unknown) => rejections.push(e)
    process.on('unhandledRejection', onRejection)
    try {
      await import(module)
      await new Promise((r) => setTimeout(r, 20))
    } finally {
      process.off('unhandledRejection', onRejection)
    }
    expect(rejections).toEqual([])
    expect(document.getElementById('not-installed')?.textContent).toContain(`This copy of ${name} was opened as a file`)
    expect(document.querySelector(gone)).toBeNull()
  })
})
