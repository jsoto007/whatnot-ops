import type { CapturedRow } from '../src/capture/types'
import { HOST_ID } from '../src/content/panel'

const SHOW = '2026-11-07T00:00:00.000Z'
const row = (i: number, username: string, body: string): CapturedRow => ({
  id: `r${i}`,
  showId: SHOW,
  username,
  body,
  rawText: `${username} ${body}`,
  capturedAt: new Date(Date.parse(SHOW) + i * 1000).toISOString(),
  seq: i + 1,
})

/** The file's bytes: readAsText would drop the byte-order mark the test wants to see. */
const readBlob = (b: Blob) =>
  new Promise<Uint8Array>((resolve) => {
    const fr = new FileReader()
    fr.onload = () => resolve(new Uint8Array(fr.result as ArrayBuffer))
    fr.readAsArrayBuffer(b)
  })

describe('Stop & Save / Save backup copy', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    document.getElementById(HOST_ID)?.remove()
  })

  it('downloads the chat as a CSV spreadsheet, watch-word hits marked, formula-looking lines defused', async () => {
    vi.stubGlobal('chrome', {
      runtime: { id: 'ext', sendMessage: async () => null, onMessage: { addListener: () => {} } },
      storage: { local: { get: async () => ({}), set: async () => {}, remove: async () => {} } },
    })
    const { Controller } = await import('../src/content/controller')
    const c = new Controller()
    await c.init()
    const inner = c as unknown as { log: CapturedRow[]; startedAt: string | null; watchWords: string[]; download(label: string): void }
    inner.log = [row(0, 'kat.c', "I'll take 3"), row(1, 'troll', '=HYPERLINK("http://evil")'), row(2, 'host_shop', 'sold to kat.c')]
    inner.startedAt = SHOW
    inner.watchWords = ['sold']
    let blob: Blob | null = null
    const orig = { create: URL.createObjectURL, revoke: URL.revokeObjectURL }
    URL.createObjectURL = (b: Blob | MediaSource) => {
      blob = b as Blob
      return 'blob:chat-scraper/1'
    }
    URL.revokeObjectURL = () => {}
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    try {
      inner.download('backup')
      expect(click).toHaveBeenCalledTimes(1)
      const a = click.mock.contexts[0] as HTMLAnchorElement
      expect(a.download).toMatch(/^Whatnot_\d{4}-\d{2}-\d{2}_backup\.csv$/)
      expect(blob!.type).toBe('text/csv')
      const bytes = await readBlob(blob!)
      expect([...bytes.slice(0, 3)]).toEqual([0xef, 0xbb, 0xbf]) // UTF-8 BOM, so Excel reads emoji and accents
      const lines = new TextDecoder().decode(bytes).split('\r\n')
      expect(lines[0]).toBe('"Time","Username","Message","Flagged"')
      expect(lines[1]).toMatch(/^"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}","kat\.c","I'll take 3",""$/)
      expect(lines[2]).toContain('"troll","\'=HYPERLINK(""http://evil"")",""')
      expect(lines[3]).toContain('"host_shop","sold to kat.c","yes"')
      expect(lines[4]).toBe('')
    } finally {
      click.mockRestore()
      URL.createObjectURL = orig.create
      URL.revokeObjectURL = orig.revoke
      c.destroy()
    }
  })
})
