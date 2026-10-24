import { readFileSync } from 'node:fs'
import path from 'node:path'

const html = readFileSync(path.resolve(__dirname, '..', 'options.html'), 'utf8')

describe('Settings page opened as a file (no extension APIs)', () => {
  beforeEach(() => {
    vi.resetModules()
    document.body.innerHTML = html.slice(html.indexOf('<header>'), html.indexOf('<script'))
    // Chrome defines window.chrome on ordinary pages too, just without storage/runtime.
    vi.stubGlobal('chrome', {})
  })
  afterEach(() => vi.unstubAllGlobals())

  it('says how to open it from Chrome instead of throwing on chrome.storage', async () => {
    const rejections: unknown[] = []
    const onRejection = (e: unknown) => rejections.push(e)
    process.on('unhandledRejection', onRejection)
    try {
      await import('../src/options/index')
      await new Promise((r) => setTimeout(r, 20))
    } finally {
      process.off('unhandledRejection', onRejection)
    }
    expect(rejections).toEqual([])
    expect(document.getElementById('not-installed')?.textContent).toMatch(/opened as a file/)
    expect(document.getElementById('pair')).toBeNull()
  })
})
