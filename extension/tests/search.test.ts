import { readFileSync } from 'node:fs'
import path from 'node:path'
import { IDBFactory } from 'fake-indexeddb'
import type { CapturedRow } from '../src/capture/types'
import { putRows, resetDbForTests } from '../src/storage/db'

const SHOW = '2026-11-07T00:00:00.000Z'
const ref = { id: SHOW, startedAt: SHOW, url: '/live/x' }
const N = 30_000

/** Every 1000th line is jess_pulls (the last one too); everyone else is u<n>. */
function row(i: number, username = i % 1000 === 999 ? 'jess_pulls' : `u${i % 997}`, body = `msg ${i} I'll take number ${i % 90}`): CapturedRow {
  return { id: `r${i}`, showId: SHOW, username, body, rawText: `${username} ${body}`, capturedAt: new Date(Date.parse(SHOW) + i * 500).toISOString(), seq: i + 1 }
}

const body = () => {
  const html = readFileSync(path.resolve(__dirname, '..', 'search.html'), 'utf8')
  return html.slice(html.indexOf('<header>'), html.indexOf('<script'))
}
const $ = (id: string) => document.getElementById(id)!
const lines = () => $('results').querySelectorAll('.line')

let onMsg: (m: { cmd: string; showId: string }) => void

/** Open Chat Search on what is saved now, and wait for it to show the newest show. */
async function open(): Promise<void> {
  vi.resetModules()
  document.body.innerHTML = body()
  vi.stubGlobal('chrome', { runtime: { id: 'ext', onMessage: { addListener: (f: typeof onMsg) => (onMsg = f) }, openOptionsPage() {} } })
  await import('../src/search/index')
  await vi.waitFor(() => expect($('summary').textContent).toContain('messages in this show'), { timeout: 10_000 })
}

function search(q: string, ctx = '2'): void {
  ;($('q') as HTMLInputElement).value = q
  ;($('ctx') as HTMLSelectElement).value = ctx
  $('go').click()
}

describe('Chat Search on a 30k-line show', () => {
  beforeAll(async () => {
    globalThis.indexedDB = new IDBFactory()
    resetDbForTests()
    const all = Array.from({ length: N }, (_, i) => row(i))
    // A viewer typing markup: it must show as text.
    all[5] = row(5, 'u5', '<img src=x onerror="alert(1)"> <b>e</b>')
    for (let i = 0; i < N; i += 1000) await putRows(ref, all.slice(i, i + 1000))
  }, 60_000)
  afterEach(() => vi.unstubAllGlobals())

  it('draws the first 500 matches, says how many there are, and draws 500 more on request', async () => {
    await open()
    expect($('summary').textContent).toBe(`${N} messages in this show. Type a buyer name or word to search.`)
    search('e') // matches every line
    expect(lines().length).toBe(500)
    expect($('summary').textContent).toBe(`${N} messages matching "e" out of ${N}. Showing the first 500.`)
    expect($('more').hidden).toBe(false)
    expect($('moreBtn').textContent).toBe('Show 500 more')
    // Chat text is text: no element made from what a viewer typed.
    const typed = [...lines()].find((l) => l.textContent!.includes('onerror'))!
    expect(typed.querySelector('img, b')).toBeNull()
    expect(typed.textContent).toContain('<img src=x onerror="alert(1)"> <b>e</b>')

    $('moreBtn').click()
    expect(lines().length).toBe(1000)
    expect($('summary').textContent).toContain('Showing the first 1000.')
    // Every line drawn once, in order: the next lot carries on where the first stopped.
    const texts = [...lines()].map((l) => l.querySelector('span:not(.t)')!.textContent)
    expect(new Set(texts).size).toBe(1000)
    expect(texts[500]).toContain('msg 500 ')

    search('jess_pulls')
    expect($('results').querySelectorAll('.line.m').length).toBe(30)
    expect($('more').hidden).toBe(true)
    expect($('summary').textContent).toBe(`30 messages matching "jess_pulls" out of ${N}.`)
  })

  it('a live update reads and draws only the new lines; what is on screen stays', async () => {
    await open()
    search('jess_pulls')
    const cards = () => [...$('results').querySelectorAll('.hit')]
    const [first, last] = [cards()[0]!, cards()[29]!]
    expect(last.querySelectorAll('.line').length).toBe(3) // line 29999 is the show's last: no lines after it yet
    const getAll = vi.spyOn(IDBIndex.prototype, 'getAll')

    // 50 more lines while recording; the second is jess_pulls again, close to the last match.
    await putRows(
      ref,
      Array.from({ length: 50 }, (_, k) => row(N + k, k === 1 ? 'jess_pulls' : 'u1')),
    )
    onMsg({ cmd: 'showUpdated', showId: SHOW })
    await vi.waitFor(() => expect($('summary').textContent).toBe(`31 messages matching "jess_pulls" out of ${N + 50}.`))

    // Only the rows after the last one read came out of IndexedDB.
    expect(getAll.mock.calls.map(([range]) => (range as IDBKeyRange).lower)).toEqual([[SHOW, N]])
    getAll.mockRestore()
    // The same cards, not a redraw; the new match joined the last card with the lines around it.
    expect(cards()).toHaveLength(30)
    expect(cards()[0]).toBe(first)
    expect(cards()[29]).toBe(last)
    expect(last.querySelector('.hit-head span')!.textContent).toContain('· 2 matches')
    expect([...last.querySelectorAll('.line')].map((l) => l.classList.contains('m'))).toEqual([false, false, true, false, true, false, false])
  })

  it('loads a CSV it wrote (and still the old .txt) as a show to search', async () => {
    await open()
    const csv = '\uFEFF"Time","Username","Message","Flagged"\r\n"2026-11-07 20:01:05","kat.c","I\'ll take 3","yes"\r\n"2026-11-07 20:01:09","troll","\'=SUM(1)",""\r\n'
    const input = $('file') as HTMLInputElement
    const file = new File([csv], 'Whatnot_2026-11-07_backup.csv', { type: 'text/csv' })
    // jsdom's File has no text(); Chrome's does.
    file.text ??= () => new Promise((resolve) => Object.assign(new FileReader(), { onload(this: FileReader) { resolve(this.result as string) } }).readAsText(file))
    Object.defineProperty(input, 'files', { value: [file], configurable: true })
    input.dispatchEvent(new Event('change'))
    await vi.waitFor(() => expect($('summary').textContent).toContain('2 messages'))
    search('take')
    expect([...lines()].map((l) => l.textContent)).toEqual(expect.arrayContaining([expect.stringContaining("kat.c I'll take 3")]))
    search('SUM')
    expect([...lines()].map((l) => l.textContent?.includes('troll =SUM(1)'))).toContain(true) // the apostrophe was only for Excel
  })

  it('Download writes a CSV spreadsheet and frees the file it made', async () => {
    await open()
    // jsdom has no object URLs.
    const orig = { create: URL.createObjectURL, revoke: URL.revokeObjectURL }
    const revoke = vi.fn()
    let blob: Blob | null = null
    URL.createObjectURL = (b: Blob | MediaSource) => {
      blob = b as Blob
      return 'blob:chat-search/1'
    }
    URL.revokeObjectURL = revoke
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      $('dl').click()
      expect(click).toHaveBeenCalledTimes(1)
      const a = click.mock.contexts[0] as HTMLAnchorElement
      expect(a.download).toMatch(/^Whatnot_\d{4}-\d{2}-\d{2}_show\.csv$/)
      expect(blob!.type).toBe('text/csv')
      const text = await new Promise<string>((resolve) => {
        const fr = new FileReader()
        fr.onload = () => resolve(fr.result as string)
        fr.readAsText(blob!)
      })
      expect(text.startsWith('"Time","Username","Message","Flagged"\r\n')).toBe(true) // readAsText drops the BOM (download.test.ts checks it)
      expect(text).toContain('","u1","msg 1 I\'ll take number 1",""\r\n')
      expect(revoke).not.toHaveBeenCalled()
      vi.advanceTimersByTime(5000)
      expect(revoke).toHaveBeenCalledWith('blob:chat-search/1')
    } finally {
      vi.useRealTimers()
      click.mockRestore()
      URL.createObjectURL = orig.create
      URL.revokeObjectURL = orig.revoke
    }
  })
})
