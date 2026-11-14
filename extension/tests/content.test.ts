import v8 from 'node:v8'
import vm from 'node:vm'
import type { CapturedRow } from '../src/capture/types'
import { HOST_ID, Panel } from '../src/content/panel'

// A real full GC, so a test can tell whether a retired Controller is still reachable.
v8.setFlagsFromString('--expose-gc')
const gc = vm.runInNewContext('gc') as () => void
const collect = async () => {
  await new Promise((r) => setTimeout(r, 20))
  gc()
  gc()
}

const SHOW = '2026-11-07T00:00:00.000Z'
const row = (i: number, username = `user${i % 300}`): CapturedRow => ({
  id: `r${i}`,
  showId: SHOW,
  username,
  body: `msg ${i} I'll take number ${i % 90}`,
  rawText: `${username} msg ${i} I'll take number ${i % 90}`,
  capturedAt: new Date(Date.parse(SHOW) + i * 1000).toISOString(),
  seq: i + 1,
})

/** Window 'resize' listeners still registered. Held weakly: the tracker itself must keep nothing alive. */
function trackResize(): { live: () => number; restore: () => void } {
  const add = window.addEventListener
  const remove = window.removeEventListener
  const fns: WeakRef<EventListenerOrEventListenerObject>[] = []
  window.addEventListener = function (type: string, fn: EventListenerOrEventListenerObject, o?: boolean | AddEventListenerOptions) {
    if (type === 'resize' && !fns.some((f) => f.deref() === fn)) fns.push(new WeakRef(fn))
    return add.call(window, type, fn, o)
  } as typeof window.addEventListener
  window.removeEventListener = function (type: string, fn: EventListenerOrEventListenerObject, o?: boolean | EventListenerOptions) {
    const i = fns.findIndex((f) => f.deref() === fn)
    if (type === 'resize' && i > -1) fns.splice(i, 1)
    return remove.call(window, type, fn, o)
  } as typeof window.removeEventListener
  return {
    live: () => fns.length,
    restore: () => {
      window.addEventListener = add
      window.removeEventListener = remove
    },
  }
}

function stubChrome() {
  const runtime: { id?: string; sendMessage: () => Promise<unknown>; onMessage: { addListener: () => void } } = {
    id: 'ext',
    sendMessage: async () => null,
    onMessage: { addListener: () => {} },
  }
  vi.stubGlobal('chrome', { runtime, storage: { local: { get: async () => ({}), set: async () => {}, remove: async () => {} } } })
  return runtime
}

/** A 20k-line show, built where no local of the test keeps it. */
const fill = (c: object) => {
  ;(c as { log: CapturedRow[] }).log = Array.from({ length: 20_000 }, (_, i) => row(i))
}

let tracker: ReturnType<typeof trackResize>
beforeEach(() => {
  tracker = trackResize()
})
afterEach(() => {
  tracker.restore()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  delete window.__csAlive
  document.getElementById(HOST_ID)?.remove()
})

describe('retired content script', () => {
  it('a panel taken off the page leaves no window listener behind', () => {
    const p = new Panel(document)
    p.mount(document)
    expect(tracker.live()).toBe(1)
    p.unmount()
    expect(tracker.live()).toBe(0)
    expect(document.getElementById(HOST_ID)).toBeNull()
  })

  it('a destroyed Controller can be collected with its whole show log', async () => {
    stubChrome()
    const { Controller } = await import('../src/content/controller')
    let ref: WeakRef<object> | null = null
    await (async () => {
      const c = new Controller()
      await c.init()
      fill(c)
      c.destroy()
      ref = new WeakRef(c)
    })()
    expect(tracker.live()).toBe(0)
    await collect()
    expect(ref!.deref()).toBeUndefined()
  })

  it('after an extension update the old copy goes, panel, listeners, Controller and all', async () => {
    const runtime = stubChrome()
    vi.resetModules()
    const { Controller } = await import('../src/content/controller')
    let ref: WeakRef<object> | null = null
    const init = Controller.prototype.init
    Controller.prototype.init = function (this: InstanceType<typeof Controller>) {
      ref = new WeakRef(this)
      Controller.prototype.init = init
      return init.call(this)
    }
    await import('../src/content/index')
    await new Promise((r) => setTimeout(r, 20))
    fill(ref!.deref()!)
    expect(document.getElementById(HOST_ID)).not.toBeNull()

    // A page script sending the event while this copy is still connected changes nothing.
    document.dispatchEvent(new CustomEvent('chat-scraper:takeover'))
    expect(document.getElementById(HOST_ID)).not.toBeNull()

    // Extension updated: this copy is cut off and a fresh copy announces itself on the page.
    delete runtime.id
    document.dispatchEvent(new CustomEvent('chat-scraper:takeover'))
    expect(document.getElementById(HOST_ID)).toBeNull()
    expect(tracker.live()).toBe(0)
    await collect()
    expect(ref!.deref()).toBeUndefined()
  })
})

describe('Find buyer', () => {
  it('refreshes while lines keep arriving faster than its delay', async () => {
    vi.useFakeTimers()
    stubChrome()
    const { Controller, FIND_MS } = await import('../src/content/controller')
    const c = new Controller()
    const priv = c as unknown as { findTerm: string; onRow(r: CapturedRow): void }
    const shown = () => [...c.panel.el('fr').querySelectorAll('.fi')].map((d) => d.textContent ?? '')
    priv.findTerm = 'buyer7'
    // A line every 100 ms for 2 s: a debounce of FIND_MS would not refresh until 300 ms after the last.
    for (let i = 0; i < 20; i++) {
      priv.onRow(row(i, `buyer${i % 10}`))
      vi.advanceTimersByTime(100)
    }
    expect(shown().some((t) => t.includes('buyer7 msg 7'))).toBe(true)
    // At most FIND_MS after a matching line it is listed, even though lines never paused.
    priv.onRow(row(20, 'buyer7'))
    vi.advanceTimersByTime(FIND_MS)
    expect(shown()[0]).toContain('buyer7 msg 20')
    c.destroy()
  })
})

describe('wins line', () => {
  it('on the seller page, says what the laptop is for once the web app takes winners from Whatnot', async () => {
    stubChrome()
    const { Controller } = await import('../src/content/controller')
    const c = new Controller()
    type Needs = { saleMode: 'winners_pick' | 'listing'; winnersFromWhatnot: boolean } | null
    const priv = c as unknown as { wins: object | null; setNeeds(n: Needs): void; stopStock(): void; renderWins(): void }
    const line = () => (c.panel.el('wins').hidden ? '' : c.panel.el('wins').textContent)
    history.replaceState(null, '', '/dashboard/live/show-1')
    priv.wins = {}
    priv.renderWins()
    expect(line()).toContain('Winners are not shown on the seller page')
    // Orders are only compared, not applied: the laptop still has to read the winners.
    priv.setNeeds({ saleMode: 'winners_pick', winnersFromWhatnot: false })
    expect(line()).toContain('Winners are not shown on the seller page')
    priv.setNeeds({ saleMode: 'winners_pick', winnersFromWhatnot: true })
    expect(line()).toBe('Winners come from Whatnot. This laptop records the chat, where winners pick their product.')
    priv.setNeeds({ saleMode: 'listing', winnersFromWhatnot: true })
    expect(line()).toBe('Winners and products come from Whatnot for this show. No recording is needed.')
    // Recording stopped: nothing is assumed about the next show.
    priv.stopStock()
    priv.renderWins()
    expect(line()).toContain('Winners are not shown on the seller page')
    history.replaceState(null, '', '/live/show-1')
    c.destroy()
  })

  it("says the seller's live page shows no winners, until a win is read", async () => {
    stubChrome()
    const { Controller } = await import('../src/content/controller')
    const c = new Controller()
    const priv = c as unknown as { wins: object | null; log: CapturedRow[]; renderWins(): void }
    const line = () => (c.panel.el('wins').hidden ? '' : c.panel.el('wins').textContent)
    history.replaceState(null, '', '/dashboard/live/show-1')
    priv.renderWins()
    expect(line()).toBe('') // not recording
    priv.wins = {}
    priv.renderWins()
    expect(line()).toContain('Winners are not shown on the seller page')
    const body = 'kat.c won the auction for CHOOSE ANY PRODUCT YOU WANT #11 for $13.00'
    priv.log = [{ ...row(1), username: '', body, rawText: body, source: 'player' }]
    priv.renderWins()
    expect(line()).toBe(`wins 1 · last: ${body}`)
    history.replaceState(null, '', '/live/show-1')
    priv.log = []
    priv.renderWins()
    expect(line()).toBe('') // the buyer's page: nothing to warn about
    c.destroy()
  })
})

describe('quiet chat', () => {
  it('says so after two minutes without a chat line, on the timer, and takes it back when chat arrives', async () => {
    stubChrome()
    const { Controller, QUIET_MS } = await import('../src/content/controller')
    const c = new Controller()
    const priv = c as unknown as {
      wins: { check(): null } | null
      recorder: { attached: boolean } | null
      lastChatAt: number
      checkPlayer(timer?: boolean): void
      chatArrived(): void
    }
    const status = () => c.panel.el('st').textContent
    priv.wins = { check: () => null } // recording, no win on screen
    priv.recorder = { attached: false }
    priv.lastChatAt = Date.now() - QUIET_MS
    priv.checkPlayer(true)
    expect(status()).not.toContain('No new chat') // the chat is not on screen: that has its own line
    priv.recorder = { attached: true }
    priv.checkPlayer() // a change in the player is not the timer
    expect(status()).not.toContain('No new chat')
    priv.checkPlayer(true)
    expect(status()).toContain('No new chat for 2 minutes')
    priv.chatArrived()
    expect(status()).toBe('Watching chat.')
    // Another status replaced the words: a chat line leaves that one alone.
    priv.lastChatAt = Date.now() - QUIET_MS
    priv.checkPlayer(true)
    c.panel.setStatus('Downloaded 12 messages.', 'good')
    priv.chatArrived()
    expect(status()).toBe('Downloaded 12 messages.')
    priv.recorder = null
    priv.wins = null
    c.destroy()
  })
})
