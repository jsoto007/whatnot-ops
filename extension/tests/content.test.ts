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

describe('page-wide decline scan', () => {
  it('is skipped while nothing outside the chat and the panel changed, and runs again on a change, a holding decline or the backstop', async () => {
    stubChrome()
    vi.useFakeTimers()
    const { Controller } = await import('../src/content/controller')
    const c = new Controller()
    const chat = document.createElement('div')
    const other = document.createElement('div')
    document.body.append(chat, other)
    const pages: boolean[] = []
    const declines = {
      holding: false,
      get pageHolding() {
        return this.holding
      },
      check: (_d: Document, scan: { page: boolean }) => {
        pages.push(scan.page)
        return []
      },
      noteWin: () => {},
    }
    const priv = c as unknown as {
      wins: { check(): null } | null
      recorder: { attached: boolean; box: Element; currentSelector: string } | null
      declines: typeof declines | null
      watchPage(): void
      checkPlayer(timer?: boolean): void
    }
    priv.wins = { check: () => null }
    priv.recorder = { attached: true, box: chat, currentSelector: '' }
    priv.declines = declines
    priv.watchPage()
    const tick = async (ms = 1000) => {
      vi.advanceTimersByTime(ms)
      await Promise.resolve() // MutationObserver callbacks run as microtasks
      priv.checkPlayer(true)
    }
    await tick() // first scan: nothing has been looked at yet
    expect(pages.at(-1)).toBe(true)
    await tick()
    expect(pages.at(-1)).toBe(false) // nothing changed
    chat.append(document.createElement('p')) // chat lines arrive all the time: not a reason to walk the page
    await tick()
    expect(pages.at(-1)).toBe(false)
    c.panel.setStatus('something', 'info') // our own panel neither
    await tick()
    expect(pages.at(-1)).toBe(false)
    const player = document.createElement('div')
    document.body.append(player)
    await tick() // adding it is a change outside the chat
    ;(c as unknown as { observedPlayer: Element | null }).observedPlayer = player
    player.textContent = '0:09' // the auction clock ticks every second: read on its own changes
    await tick()
    expect(pages.at(-1)).toBe(false)
    other.append(document.createElement('p')) // a Sold-tab row, say
    await tick()
    expect(pages.at(-1)).toBe(true)
    await tick()
    expect(pages.at(-1)).toBe(false)
    declines.holding = true // a decline is waiting to be held: it needs a scan that still sees it
    await tick()
    expect(pages.at(-1)).toBe(true)
    declines.holding = false
    await tick()
    expect(pages.at(-1)).toBe(false)
    await tick(15_000) // the backstop: a change the observer cannot see
    expect(pages.at(-1)).toBe(true)
    priv.recorder = null
    priv.wins = null
    priv.declines = null
    c.destroy()
    chat.remove()
    other.remove()
    player.remove()
  })
})

describe('channel (made-up names; where Whatnot shows the seller is not verified)', () => {
  type Msg = { cmd: string; show?: { id: string; channel?: string; movedOnAt?: string }; rows?: CapturedRow[]; chosen?: boolean }
  const OWN = 'maple_cards_demo'
  const OTHER = 'birch_breaks_demo'
  const tick = () => new Promise((r) => setTimeout(r, 0))
  const seller = (name: string) => `<span data-testid="show-host-username">${name}</span>`

  // A test that fails half way must not leave its seller's name on the page for the next one.
  afterEach(() => document.querySelectorAll('#live-page').forEach((p) => p.remove()))

  /** A live page: the seller's name, the player and the chat. Returns what reached the background. */
  function livePage(path: string, channel: string, lines: string[]) {
    const runtime = stubChrome()
    const sent: Msg[] = []
    let session: unknown = null
    runtime.sendMessage = (async (m: Msg) => {
      sent.push(m)
      if (m.cmd === 'rows') return { ok: true, count: m.rows!.length }
      if (m.cmd === 'getSession') return session
      return null
    }) as never
    history.replaceState(null, '', path)
    const host = document.createElement('div')
    host.id = 'live-page'
    host.innerHTML = `<div id="seller">${channel ? seller(channel) : ''}</div><section data-testid="live-player"></section><div class="list" id="list"></div>`
    document.body.append(host)
    const list = host.querySelector('#list')!
    const say = (...texts: string[]) => list.insertAdjacentHTML('beforeend', texts.map((t) => `<div class="msg">${t}</div>`).join(''))
    say(...lines)
    return {
      sent,
      list,
      say,
      setSession: (s: unknown) => (session = s),
      setChannel: (name: string) => (host.querySelector('#seller')!.innerHTML = name ? seller(name) : ''),
      saved: () => sent.filter((m) => m.cmd === 'rows').flatMap((m) => m.rows!.map((r) => r.rawText)),
      cleanup: () => host.remove(),
    }
  }

  type Priv = {
    channel: string
    log: CapturedRow[]
    recorder: object | null
    wins: { check(): CapturedRow | null } | null
    startRecording(box: Element | null, spot: { selectors: string[]; page: string }): void
    picked(target: Element): void
    checkPage(): void
    checkPlayer(timer?: boolean): void
    flush(): Promise<void>
  }

  const win = (body: string): CapturedRow => ({ id: 'w' + body.length, showId: SHOW, username: '', body, rawText: body, capturedAt: SHOW, seq: 0, source: 'player' })

  it('stops when the page shows another channel, keeps nothing of it, and carries on back on its own channel', async () => {
    const page = livePage('/live/made-up-stream', OWN, ['fern_buyer first line'])
    const { Controller } = await import('../src/content/controller')
    const c = new Controller()
    const priv = c as unknown as Priv
    const status = () => c.panel.el('st').textContent ?? ''
    priv.startRecording(page.list, { selectors: ['div.msg'], page: '/live/made-up-stream' })
    // The recording remembers the channel it started on, and the background is told at once.
    expect(priv.channel).toBe(OWN)
    expect(page.sent.find((m) => m.cmd === 'recording')?.show?.channel).toBe(OWN)
    expect(status()).toBe(`Watching chat on ${OWN}'s show.`)
    page.say('oak_viewer second line')
    await tick()
    priv.wins = { check: () => win('fern_buyer won the auction for Blue bundle #4 for $3.00') }
    priv.checkPlayer(true)
    expect(priv.log.map((r) => r.rawText)).toEqual(['fern_buyer first line', 'oak_viewer second line', 'fern_buyer won the auction for Blue bundle #4 for $3.00'])

    // The show ended and Whatnot put another channel's show in this tab, at the same address.
    page.setChannel(OTHER)
    await Promise.resolve() // the page changes in a task of its own
    priv.wins = { check: () => win('pine_bidder won the auction for Red bundle #9 for $9.00') }
    priv.checkPlayer(true) // a win on the other channel's player is never kept, even before the page check
    page.list.innerHTML = '<div class="msg">pine_bidder other show line</div>'
    await tick()
    await tick()
    expect(priv.recorder).toBeNull()
    expect(status()).toContain(`Whatnot moved this tab to another channel (${OTHER})`)
    expect(status()).toContain(`${OWN}'s show`)
    page.say('pine_bidder still the other show')
    await tick()
    priv.checkPage()
    expect(priv.recorder).toBeNull()
    expect(priv.log).toHaveLength(3)
    // The other channel's name goes away (a re-render) while its chat is still on screen: not a
    // reason to read the page again. Only the recording's own channel ends the pause.
    page.setChannel('')
    priv.checkPage()
    await tick()
    expect(priv.recorder).toBeNull()
    expect(status()).toContain(`another channel (${OTHER})`)
    expect(priv.log).toHaveLength(3)

    // Back on the shop's own show: it carries on by itself.
    page.setChannel(OWN)
    page.list.innerHTML = '<div class="msg">fern_buyer first line</div><div class="msg">oak_viewer second line</div>'
    priv.checkPage()
    expect(priv.recorder).not.toBeNull()
    page.say('fern_buyer back again')
    await tick()
    await priv.flush()
    expect(page.saved()).toEqual(['fern_buyer first line', 'oak_viewer second line', 'fern_buyer won the auction for Blue bundle #4 for $3.00', 'fern_buyer back again'])
    expect(page.saved().some((t) => t.includes('pine_bidder'))).toBe(false)
    expect(page.sent.filter((m) => m.cmd === 'rows').every((m) => m.show?.channel === OWN)).toBe(true)
    expect(page.sent.filter((m) => m.cmd === 'rows').flatMap((m) => m.rows!).every((r) => r.channel === OWN)).toBe(true)
    priv.wins = null
    c.destroy()
    page.cleanup()
  })

  it('records as before when no channel can be read, and says so in what it sends', async () => {
    const page = livePage('/live/made-up-stream', '', ['fern_buyer first line'])
    const { Controller } = await import('../src/content/controller')
    const c = new Controller()
    const priv = c as unknown as Priv
    priv.startRecording(page.list, { selectors: ['div.msg'], page: '/live/made-up-stream' })
    expect(c.panel.el('st').textContent).toBe('Watching chat.')
    page.say('oak_viewer second line')
    await tick()
    await priv.flush()
    expect(page.saved()).toEqual(['fern_buyer first line', 'oak_viewer second line'])
    expect(page.sent.filter((m) => m.cmd === 'rows').every((m) => m.show?.channel === '')).toBe(true)
    expect(page.sent.filter((m) => m.cmd === 'rows').flatMap((m) => m.rows!).every((r) => r.channel === undefined)).toBe(true)
    // A name shows up later on the same page: never the recording's (it may be another seller's
    // show Whatnot moved the tab to). Capture pauses until the host says it is theirs.
    page.setChannel(OWN)
    page.say('fern_buyer third line')
    await tick()
    await tick() // the page check the refused line asked for
    expect(priv.channel).toBe('')
    expect(priv.recorder).toBeNull()
    expect(priv.log.map((r) => r.rawText)).toEqual(['fern_buyer first line', 'oak_viewer second line'])
    c.destroy()
    page.cleanup()
  })

  it('a name read only after Start is never taken as the recording\'s channel: it pauses, keeps nothing of it, and asks the host', async () => {
    // The verifier's P1: Start with the seller's name unreadable; Whatnot then moves the tab to
    // another seller's show at the SAME address, and that seller's name can be read.
    const page = livePage('/live/made-up-stream', '', ['fern_buyer first line'])
    const { Controller } = await import('../src/content/controller')
    const c = new Controller()
    const priv = c as unknown as Priv
    const status = () => c.panel.el('st').textContent ?? ''
    const moves = () => page.sent.filter((m) => m.show?.movedOnAt)
    priv.startRecording(page.list, { selectors: ['div.msg'], page: '/live/made-up-stream' })
    await tick()
    page.say('oak_viewer second line')
    await tick()
    expect(priv.channel).toBe('')
    page.setChannel(OTHER)
    page.list.innerHTML = '<div class="msg">pine_bidder other show line</div>'
    await tick()
    await tick()
    priv.checkPage()
    page.say('pine_bidder second other line')
    await tick()
    expect(priv.channel).toBe('') // never adopted
    expect(priv.recorder).toBeNull()
    expect(status()).toBe(
      `Paused: the channel ${OTHER} showed up on this tab after recording started, so it may be another seller's show. ` +
        'Nothing from it is recorded. Check the tab: if it is your show, click Pick chat, then one chat message. Otherwise go back to your show or press Stop.',
    )
    expect(status()).not.toContain('another channel (') // a doubt, not a fact
    // The button the words name is the one on screen.
    const start = c.panel.el<HTMLButtonElement>('start')
    expect([start.hidden, start.textContent]).toEqual([false, 'Pick chat'])
    // The name goes away again (as the page looked at Start): still a doubt, still paused.
    page.setChannel('')
    page.say('pine_bidder third other line')
    await tick()
    priv.checkPage()
    expect(priv.recorder).toBeNull()
    expect(status()).toContain(`the channel ${OTHER} showed up`)
    expect(priv.log.map((r) => r.rawText)).toEqual(['fern_buyer first line', 'oak_viewer second line'])
    expect(moves()).toEqual([]) // a doubt is not a move: the server is not told the show ended
    await priv.flush()
    const saves = page.sent.filter((m) => m.cmd === 'rows')
    expect(saves.flatMap((m) => m.rows!.map((r) => r.rawText))).toEqual(['fern_buyer first line', 'oak_viewer second line'])
    // Nothing the laptop keeps names the other seller: the shop's lines go up with no channel, as before.
    expect(page.sent.some((m) => m.show?.channel === OTHER)).toBe(false)
    expect(saves.flatMap((m) => m.rows!).every((r) => r.channel === undefined)).toBe(true)

    // The tab comes back to the shop's own show, whose name can now be read: still a doubt.
    page.setChannel(OWN)
    page.list.innerHTML = ''
    page.say('fern_buyer back on the own show', 'oak_viewer hello again', 'fern_buyer one more')
    priv.checkPage()
    await tick()
    expect(priv.recorder).toBeNull()
    expect(priv.channel).toBe('')
    expect(status()).toContain(OWN)
    expect(status()).toContain('Pick chat')
    // The host checks the tab and says it is theirs (Pick chat, then a line): now the channel is fixed.
    priv.picked(page.list.firstElementChild!)
    expect(priv.channel).toBe(OWN)
    expect(priv.recorder).not.toBeNull()
    const fixed = page.sent.filter((m) => m.cmd === 'recording').at(-1)!
    expect(fixed.show?.channel).toBe(OWN)
    expect(fixed.chosen).toBe(true)
    page.say('fern_buyer after the host said so')
    await tick()
    await priv.flush()
    const rows = page.sent.filter((m) => m.cmd === 'rows').flatMap((m) => m.rows!)
    expect(rows.find((r) => r.rawText === 'fern_buyer after the host said so')?.channel).toBe(OWN)
    expect(rows.some((r) => r.rawText.includes('pine_bidder'))).toBe(false)
    expect(rows.filter((r) => r.rawText === 'fern_buyer first line' || r.rawText === 'oak_viewer second line').every((r) => r.channel === undefined)).toBe(true)
    c.destroy()
    page.cleanup()
  })

  it('each line carries the channel read when it was captured, or none', async () => {
    const page = livePage('/live/made-up-stream', OWN, ['fern_buyer first line'])
    const { Controller } = await import('../src/content/controller')
    const c = new Controller()
    const priv = c as unknown as Priv
    priv.startRecording(page.list, { selectors: ['div.msg'], page: '/live/made-up-stream' })
    await tick() // Whatnot changes the page in a task of its own
    page.setChannel('') // a re-render: the name cannot be read for a moment
    page.say('oak_viewer while the name is away')
    await tick()
    page.setChannel(OWN)
    page.say('fern_buyer name back')
    await tick()
    priv.wins = { check: () => win('fern_buyer won the auction for Blue bundle #4 for $3.00') }
    priv.checkPlayer(true)
    priv.wins = null
    const expected = [
      ['fern_buyer first line', OWN],
      ['oak_viewer while the name is away', undefined],
      ['fern_buyer name back', OWN],
      ['fern_buyer won the auction for Blue bundle #4 for $3.00', OWN],
    ]
    expect(priv.log.map((r) => [r.rawText, r.channel])).toEqual(expected)
    await priv.flush()
    expect(page.sent.filter((m) => m.cmd === 'rows').flatMap((m) => m.rows!).map((r) => [r.rawText, r.channel])).toEqual(expected)
    c.destroy()
    page.cleanup()
  })

  it('a reloaded recording with no channel does not take the one the page now shows', async () => {
    const page = livePage('/live/made-up-stream', OTHER, ['pine_bidder other show line'])
    const spot = { selectors: ['div.msg'], page: '/live/made-up-stream' }
    page.setSession({ show: { id: SHOW, startedAt: SHOW, updatedAt: Date.now(), count: 1, date: '2026-11-06', channel: '' }, rows: [row(1)], recording: spot })
    const { Controller } = await import('../src/content/controller')
    const c = new Controller()
    const priv = c as unknown as Priv
    await c.init()
    expect(priv.channel).toBe('')
    expect(priv.recorder).toBeNull()
    expect(c.panel.el('st').textContent).toContain(`the channel ${OTHER} showed up`)
    expect(c.panel.el('st').textContent).toContain('Pick chat')
    const start = c.panel.el<HTMLButtonElement>('start')
    expect([start.hidden, start.textContent]).toEqual([false, 'Pick chat'])
    expect(page.sent.some((m) => m.show?.channel === OTHER)).toBe(false)
    expect(page.sent.some((m) => m.show?.movedOnAt)).toBe(false)
    expect(priv.log).toHaveLength(1)
    c.destroy()
    page.cleanup()
  })

  it('a recording started on a channel page keeps the handle in that address, whatever page the tab is on later', async () => {
    // A recording from before channels were read (none stored), started at /user/<handle>.
    const page = livePage(`/user/${OTHER}`, '', ['pine_bidder other show line'])
    const spot = { selectors: ['div.msg'], page: `/user/${OWN}` }
    page.setSession({ show: { id: SHOW, startedAt: SHOW, updatedAt: Date.now(), count: 1, date: '2026-11-06' }, rows: [row(1)], recording: spot })
    const { Controller } = await import('../src/content/controller')
    const c = new Controller()
    const priv = c as unknown as Priv
    await c.init()
    expect(priv.channel).toBe(OWN)
    expect(priv.recorder).toBeNull()
    expect(c.panel.el('st').textContent).toContain(`another channel (${OTHER})`)
    c.destroy()
    page.cleanup()
  })

  it('a recording started at a channel page keeps nothing of another channel page the tab moves to, and carries on back (verifier P2)', async () => {
    const page = livePage(`/user/${OWN}`, '', ['fern_buyer first line'])
    const { Controller } = await import('../src/content/controller')
    const c = new Controller()
    const priv = c as unknown as Priv
    priv.startRecording(page.list, { selectors: ['div.msg'], page: `/user/${OWN}` })
    expect(priv.channel).toBe(OWN)
    await tick() // Whatnot changes the page in a task of its own
    history.replaceState(null, '', `/user/${OTHER}`)
    page.list.innerHTML = '<div class="msg">pine_bidder other show line</div>'
    await tick()
    await tick()
    priv.checkPage()
    expect(priv.recorder).toBeNull()
    expect(c.panel.el('st').textContent).toContain(`another channel (${OTHER})`)
    history.replaceState(null, '', `/user/${OWN}`)
    page.list.innerHTML = '<div class="msg">fern_buyer back</div>'
    priv.checkPage()
    page.say('fern_buyer later line')
    await tick()
    await priv.flush()
    const rows = page.sent.filter((m) => m.cmd === 'rows').flatMap((m) => m.rows!)
    expect(rows.some((r) => r.rawText.includes('pine_bidder'))).toBe(false)
    expect(rows.map((r) => [r.rawText, r.channel])).toEqual([
      ['fern_buyer first line', OWN],
      ['fern_buyer back', OWN],
      ['fern_buyer later line', OWN],
    ])
    c.destroy()
    page.cleanup()
  })

  it("will not point a recording at another channel's chat", async () => {
    const page = livePage('/live/made-up-stream', OWN, ['fern_buyer first line'])
    const { Controller } = await import('../src/content/controller')
    const c = new Controller()
    const priv = c as unknown as Priv
    priv.startRecording(page.list, { selectors: ['div.msg'], page: '/live/made-up-stream' })
    await tick() // Whatnot changes the page in a task of its own
    page.setChannel(OTHER)
    page.list.innerHTML = '<div class="msg">pine_bidder other show line</div>'
    await tick()
    await tick()
    // The host clicks a line of the other show ("Pick chat"): refused, the recording keeps its page.
    priv.picked(page.list.firstElementChild!)
    expect(c.panel.el('st').textContent).toContain(`another channel (${OTHER})`)
    expect(priv.recorder).toBeNull()
    expect(priv.log.map((r) => r.rawText)).toEqual(['fern_buyer first line'])
    c.destroy()
    page.cleanup()
  })

  it('notes once when the page moved on to another channel, and again only after it came back', async () => {
    const page = livePage('/live/made-up-stream', OWN, ['fern_buyer first line'])
    const { Controller } = await import('../src/content/controller')
    const c = new Controller()
    const priv = c as unknown as Priv
    const moves = () => [...new Set(page.sent.filter((m) => m.show?.movedOnAt).map((m) => m.show!.movedOnAt))]
    priv.startRecording(page.list, { selectors: ['div.msg'], page: '/live/made-up-stream' })
    expect(moves()).toEqual([])
    page.setChannel(OTHER)
    priv.checkPage()
    expect(moves()).toHaveLength(1) // the background is told at once (it tells the server)
    expect(page.sent.some((m) => m.cmd === 'recording' && m.show?.movedOnAt === moves()[0])).toBe(true)
    priv.checkPage()
    page.setChannel('')
    priv.checkPage()
    expect(moves()).toHaveLength(1) // one move, however often the page is looked at
    // An address change alone is not a move: only another channel is.
    page.setChannel(OWN)
    page.list.innerHTML = '<div class="msg">fern_buyer back</div>'
    priv.checkPage()
    expect(priv.recorder).not.toBeNull()
    await new Promise((r) => setTimeout(r, 5))
    page.setChannel(OTHER)
    priv.checkPage()
    expect(moves()).toHaveLength(2)
    expect(moves()[1]! > moves()[0]!).toBe(true)
    c.destroy()
    page.cleanup()
  })

  it('a page reloaded while still on another channel keeps the time of the move it noted', async () => {
    const page = livePage('/live/made-up-stream', OTHER, ['pine_bidder other show line'])
    const spot = { selectors: ['div.msg'], page: '/live/made-up-stream' }
    const noted = '2026-11-07T01:00:00.000Z'
    page.setSession({ show: { id: SHOW, startedAt: SHOW, updatedAt: Date.now(), count: 1, date: '2026-11-06', channel: OWN, movedOnAt: noted }, rows: [row(1)], recording: spot })
    const { Controller } = await import('../src/content/controller')
    const c = new Controller()
    await c.init()
    const said = page.sent.filter((m) => m.show?.movedOnAt).map((m) => m.show!.movedOnAt)
    expect(said.length).toBeGreaterThan(0)
    expect(said.every((t) => t === noted)).toBe(true)
    c.destroy()
    page.cleanup()
  })

  it('a reloaded page remembers the channel and does not record another one', async () => {
    const page = livePage('/live/made-up-stream', OTHER, ['pine_bidder other show line'])
    const spot = { selectors: ['div.msg'], page: '/live/made-up-stream' }
    page.setSession({ show: { id: SHOW, startedAt: SHOW, updatedAt: Date.now(), count: 1, date: '2026-11-06', channel: OWN }, rows: [row(1)], recording: spot })
    const { Controller } = await import('../src/content/controller')
    const c = new Controller()
    const priv = c as unknown as Priv
    await c.init()
    expect(priv.channel).toBe(OWN)
    expect(priv.recorder).toBeNull()
    expect(c.panel.el('st').textContent).toContain(`another channel (${OTHER})`)
    page.setChannel(OWN)
    priv.checkPage()
    expect(priv.recorder).not.toBeNull()
    c.destroy()
    page.cleanup()
  })
})
