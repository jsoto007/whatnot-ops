import { IDBFactory } from 'fake-indexeddb'
import type { CapturedRow } from '../src/capture/types'
import { KV_MIGRATED, kvSet, resetDbForTests } from '../src/storage/db'
import { UploadTimer } from '../src/upload/schedule'
import { KV_CONNECTION } from '../src/upload/settings'

const SERVER = 'https://ops.test'
const SHOW = '2026-11-07T00:00:00.000Z'
type Listener = (msg: unknown, sender: unknown, sendResponse: (r: unknown) => void) => boolean

/** Real event-loop turns: fake-indexeddb runs on setImmediate, which these tests leave real. */
async function settle(turns = 200): Promise<void> {
  for (let i = 0; i < turns; i++) await new Promise((r) => setImmediate(r))
}

/** Let `ms` of (fake) time pass in small steps, giving each upload real turns to finish. */
async function pass(ms: number, step = 250): Promise<void> {
  for (let t = 0; t < ms; t += step) {
    vi.advanceTimersByTime(step)
    await settle()
  }
}

describe('background uploads', () => {
  let onMessage: Listener
  let online: boolean
  let posts: number[]
  let alarms: { name: string; delayInMinutes?: number }[]
  let seq: number

  beforeEach(async () => {
    globalThis.indexedDB = new IDBFactory()
    resetDbForTests()
    vi.resetModules()
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    vi.setSystemTime(new Date(SHOW))
    online = false
    posts = []
    alarms = []
    seq = 0
    await kvSet(KV_MIGRATED, true)
    await kvSet(KV_CONNECTION, { apiKey: 'device-key', origin: SERVER })
    vi.stubGlobal('fetch', async (url: string) => {
      if (url !== SERVER + '/api/ingest') throw new Error('unexpected fetch ' + url)
      posts.push(Date.now())
      if (!online) throw new TypeError('Failed to fetch')
      return new Response('{}', { status: 200 })
    })
    const on = { addListener: () => {} }
    vi.stubGlobal('chrome', {
      runtime: {
        id: 'ext',
        getURL: (p: string) => 'chrome-extension://ext/' + p,
        getManifest: () => ({ version: '1.7.0', content_scripts: [] }),
        sendMessage: async () => undefined,
        onInstalled: on,
        onStartup: on,
        onMessage: { addListener: (f: Listener) => (onMessage = f) },
        onMessageExternal: on,
      },
      action: { onClicked: on },
      alarms: { create: (name: string, info: { delayInMinutes?: number }) => alarms.push({ name, ...info }), onAlarm: on },
      storage: {
        local: { get: async () => ({ cs_server_url: SERVER }), set: async () => {}, remove: async () => {} },
        onChanged: on,
      },
      tabs: { create: () => {}, query: async () => [], sendMessage: async () => undefined, update: async () => undefined },
      scripting: { executeScript: async () => undefined },
    })
    await import('../src/background')
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  /** One `rows` save from the content script on the show's page, as a flush sends it. */
  function save(): Promise<{ ok?: boolean }> {
    seq++
    const row: CapturedRow = { id: `r${seq}`, showId: SHOW, username: 'u', body: `m ${seq}`, rawText: `u m ${seq}`, capturedAt: SHOW, seq }
    return new Promise((resolve) =>
      onMessage(
        { cmd: 'rows', show: { id: SHOW, startedAt: SHOW, url: '/live/x' }, rows: [row] },
        { id: 'ext', url: 'https://www.whatnot.com/live/x', tab: { id: 1 } },
        resolve as (r: unknown) => void,
      ),
    )
  }

  it('during an outage, new lines wait for the backoff instead of posting every 2 s', async () => {
    const t0 = Date.now()
    // A line saved every 2.5 s for a minute while the server is unreachable.
    for (let i = 0; i < 24; i++) {
      expect((await save()).ok).toBe(true)
      await pass(2500)
    }
    // Before: a try 2 s after every save (24). Now: 2 s after the first, then 5, 10 and 20 s apart.
    expect(posts.map((t) => (t - t0) / 1000)).toEqual([2, 7, 17, 37])
    expect(alarms.filter((a) => a.name === 'cs_upload_retry').map((a) => a.delayInMinutes! * 60)).toEqual([5, 10, 20, 40])

    // The server is back: the next try (40 s after the last) sends everything, and new lines go 2 s after they are saved.
    online = true
    await pass(77_000 - (Date.now() - t0))
    expect(posts.map((t) => (t - t0) / 1000)).toEqual([2, 7, 17, 37, 77])
    await save()
    await pass(2000)
    expect(posts.map((t) => (t - t0) / 1000)).toEqual([2, 7, 17, 37, 77, 79])
  })

  it('a 429 waits as long as the server asks (Retry-After), not only the 5 s backoff', async () => {
    vi.stubGlobal('fetch', async () => {
      posts.push(Date.now())
      return new Response('{"error":"too many attempts"}', { status: 429, headers: { 'Retry-After': '50' } })
    })
    const t0 = Date.now()
    await save()
    await pass(2500)
    expect(posts.map((t) => (t - t0) / 1000)).toEqual([2])
    expect(alarms.filter((a) => a.name === 'cs_upload_retry').map((a) => a.delayInMinutes! * 60)).toEqual([50])
    // Lines saved meanwhile wait too: nothing goes out before the 50 s are up.
    await save()
    await pass(40_000)
    expect(posts).toHaveLength(1)
    await pass(12_000)
    expect(posts.map((t) => (t - t0) / 1000)).toEqual([2, 52])
  })

  /** One message to the background, with the given sender, as chrome would deliver it. */
  function ask(msg: unknown, sender: unknown): Promise<unknown> {
    return new Promise((resolve) => onMessage(msg, sender, resolve))
  }
  const shipment = { recipient: 'buyer1', orderDate: 'Oct 31', items: '1', itemLines: [], value: '$5.00', status: 'Ready to Ship', tracking: '', lots: [] }

  it('shipments are accepted from a Whatnot content script even when Chrome still reports the URL the page was loaded at', async () => {
    const posted: string[] = []
    vi.stubGlobal('fetch', async (url: string) => {
      posted.push(url)
      return new Response(JSON.stringify({ stored: 1, lots: 0, show: null, skipped: 0, unmatched: 1, shows: {} }), { status: 200 })
    })
    // Seller Hub was opened at /dashboard and navigated in-page to Shipments: sender.url is stale.
    const reply = await ask(
      { cmd: 'shipments', rows: [shipment], show: 'E2E Show Oct 31•8:00 PM CDT' },
      { id: 'ext', url: 'https://www.whatnot.com/dashboard', origin: 'https://www.whatnot.com', tab: { id: 1 } },
    )
    expect(reply).toMatchObject({ stored: 1, unmatched: 1 })
    expect(posted).toEqual([SERVER + '/api/ingest/shipments'])
  })

  it('shipments from an extension page, another site, or a read that is too big are refused with a reason', async () => {
    const posted: string[] = []
    vi.stubGlobal('fetch', async (url: string) => {
      posted.push(url)
      return new Response('{}', { status: 200 })
    })
    const page = { id: 'ext', url: 'chrome-extension://ext/options.html', origin: 'chrome-extension://ext' }
    expect(await ask({ cmd: 'shipments', rows: [shipment], show: '' }, page)).toEqual({ error: 'Shipments can only be sent from the Whatnot page itself.' })
    const elsewhere = { id: 'ext', url: 'https://evil.example/dashboard/shipments', origin: 'https://evil.example', tab: { id: 2 } }
    expect(await ask({ cmd: 'shipments', rows: [shipment], show: '' }, elsewhere)).toEqual({ error: 'Shipments can only be sent from the Whatnot page itself.' })
    const whatnot = { id: 'ext', url: 'https://www.whatnot.com/dashboard/shipments', origin: 'https://www.whatnot.com', tab: { id: 3 } }
    expect(await ask({ cmd: 'shipments', rows: Array.from({ length: 2001 }, () => shipment), show: '' }, whatnot)).toMatchObject({ error: expect.stringContaining('More than 2000 shipments') })
    expect(await ask({ cmd: 'shipments', rows: 'nope', show: '' }, whatnot)).toMatchObject({ error: expect.stringContaining('came back empty') })
    expect(posted).toEqual([])
  })

  it('a busy chat (a save every 1.5 s) still uploads every 2 s, not only on the minute sweep', async () => {
    online = true
    const t0 = Date.now()
    for (let i = 0; i < 20; i++) {
      await save()
      await pass(1500)
    }
    // Re-arming the 2 s timer on every save used to mean it never fired for 30 s.
    const at = posts.map((t) => (t - t0) / 1000)
    expect(at.length).toBeGreaterThanOrEqual(10)
    expect(at[0]).toBeLessThanOrEqual(2)
    for (let i = 1; i < at.length; i++) expect(at[i]! - at[i - 1]!).toBeLessThanOrEqual(3)
  })
})

describe('UploadTimer', () => {
  afterEach(() => vi.useRealTimers())

  it('waits for the backoff, never sooner than a retry already set and never pushed back', () => {
    vi.useFakeTimers()
    const t0 = Date.now()
    const fired: number[] = []
    const timer = new UploadTimer(() => fired.push(Date.now() - t0))
    timer.backOff(30_000)
    timer.soon()
    vi.advanceTimersByTime(10_000)
    timer.soon(100)
    vi.advanceTimersByTime(19_000)
    timer.soon()
    vi.advanceTimersByTime(1_000)
    expect(fired).toEqual([30_000])
    // Working again: a save uploads after the short delay.
    timer.reset()
    timer.soon()
    vi.advanceTimersByTime(2_000)
    expect(fired).toEqual([30_000, 32_000])
  })

  it('a try set before a failure waits for the backoff that failure started', () => {
    vi.useFakeTimers()
    const t0 = Date.now()
    const fired: number[] = []
    const timer = new UploadTimer(() => fired.push(Date.now() - t0))
    timer.soon() // due at 2 s
    vi.advanceTimersByTime(1_000)
    timer.backOff(5_000) // an upload failed at 1 s
    vi.advanceTimersByTime(10_000)
    expect(fired).toEqual([6_000])
  })
})
