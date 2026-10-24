import { HOLD_MS, WinWatcher, priceCents, readPlayer, winBody } from '../src/capture/player'
import { clampRow } from '../src/upload/limits'
import { validateBatch } from '../src/upload/schema'
import { bidding, idle, won } from './fixtures/player'

const SHOW = '2026-10-24T21:00:00.000Z'
const show = (html: string) => {
  document.body.innerHTML = html
  return document
}
const body = (who: string, title: string, d: string) => `${who} won the auction for ${title} for $${d}`
const T760 = '$1 PERFUMES/ COLOGNES/ NICHE WITH SAM #760'

describe('reading the player (markup copied from a live show)', () => {
  it('reads the winner, the item and the price right before "Sold", not the last-bid line', () => {
    expect(readPlayer(show(won('chonwhite828', '$188')))).toEqual({
      kind: 'won',
      win: { winner: 'chonwhite828', title: T760, priceCents: 18800 },
    })
  })

  it('bidding and an idle item are not wins', () => {
    expect(readPlayer(show(bidding('mhassell', '$140')))).toEqual({ kind: 'bidding' })
    expect(readPlayer(show(idle()))).toEqual({ kind: 'none' })
    expect(readPlayer(show('<main>no player here</main>'))).toEqual({ kind: 'none' })
  })

  it('no "Sold", no price before it, or two "Sold" is not read as a sale', () => {
    expect(readPlayer(show(won('chonwhite828', '$188').replace('>Sold<', '>Ended<')))).toEqual({ kind: 'none' })
    expect(readPlayer(show(won('chonwhite828', 'Free')))).toEqual({ kind: 'none' })
    const twice = won('chonwhite828', '$188').replace('>Sold</strong>', '>Sold</strong><strong>Sold</strong>')
    expect(readPlayer(show(twice))).toEqual({ kind: 'none' })
  })

  it('a crossed-out price before the final one does not become the price', () => {
    const html = won('chonwhite828', '$188').replace('>$188<', '>$250</strong><strong>$188<')
    expect(readPlayer(show(html))).toMatchObject({ kind: 'won', win: { priceCents: 18800 } })
  })

  it('a viewer writing "Sold" or "$1" outside the footer (e.g. chat over the video) changes nothing', () => {
    const chat = '<div class="chat"><div><strong>Sold</strong><span>$1</span></div><div><strong>viewer1</strong><span>Sold</span></div></div>'
    const html = won('chonwhite828', '$188').replace('<section class="LivePlayer_livePlayer__WDoms">', `$&${chat}`)
    expect(readPlayer(show(html))).toMatchObject({ kind: 'won', win: { priceCents: 18800 } })
  })

  it('a name Whatnot would not allow, or a price over $100,000, is not read', () => {
    expect(readPlayer(show(won('@', '$5')))).toEqual({ kind: 'none' })
    expect(readPlayer(show(won('bad\u0085name', '$5')))).toEqual({ kind: 'none' })
    expect(readPlayer(show(won('rich', '$100,000.01')))).toEqual({ kind: 'none' })
    expect(readPlayer(show(won('rich', '$100,000')))).toMatchObject({ win: { priceCents: 10_000_000 } })
  })

  it('a very long title is cut so the price stays on the line', () => {
    const state = readPlayer(show(won('chon', '$188', 'x'.repeat(3950) + ' for $5.00')))
    expect(state.kind === 'won' && state.win.title.length).toBe(240)
    expect(state.kind === 'won' && winBody(state.win).endsWith(' for $188.00')).toBe(true)
  })

  it.each([
    ['$188', 18800],
    ['$1', 100],
    ['$19.50', 1950],
    ['$1,250', 125000],
    ['$12,345.67', 1234567],
    [' $5 ', 500],
  ])('price %s is %i cents', (text, cents) => expect(priceCents(text)).toBe(cents))

  it.each(['bid $183.00', '$1.5', '$1,25', '$', '188', '$-1', '$1.234', '$12345678', 'US$5', '$１'])('%s is not a price', (text) =>
    expect(priceCents(text)).toBeNull(),
  )
})

describe('WinWatcher', () => {
  let t = 0
  const clock = () => t
  const at = (ms: number) => {
    t = ms
  }
  const watcher = () => new WinWatcher(SHOW, clock)
  beforeEach(() => at(Date.parse('2026-10-24T22:00:00.000Z')))

  it('records one row for a win that held, however long it stays up', () => {
    const w = watcher()
    expect(w.check(show(bidding('ladyyy03', '$190')))).toBeNull()
    expect(w.check(show(won('chonwhite828', '$195')))).toBeNull() // not held yet
    at(t + HOLD_MS)
    const row = w.check(show(won('chonwhite828', '$195')))
    expect(row).toEqual({
      id: expect.stringMatching(/^[0-9a-f]{16}$/),
      showId: SHOW,
      username: '',
      body: body('chonwhite828', T760, '195.00'),
      rawText: body('chonwhite828', T760, '195.00'),
      capturedAt: new Date(t).toISOString(),
      seq: 0,
      source: 'player',
    })
    at(t + 3000)
    expect(w.check(show(won('chonwhite828', '$195')))).toBeNull()
    // A re-render that drops the footer for a moment is not a second win.
    expect(w.check(show('<main></main>'))).toBeNull()
    expect(w.check(show(won('chonwhite828', '$195')))).toBeNull()
    at(t + 3000)
    expect(w.check(show(won('chonwhite828', '$195')))).toBeNull()
  })

  it('a stale first frame is not the price: the reading that held is', () => {
    const w = watcher()
    w.check(show(bidding('ladyyy03', '$180')))
    w.check(show(won('chonwhite828', '$183')))
    at(t + 200)
    expect(w.check(show(won('chonwhite828', '$188')))).toBeNull()
    at(t + HOLD_MS)
    expect(w.check(show(won('chonwhite828', '$188')))?.body).toBe(body('chonwhite828', T760, '188.00'))
  })

  it('the item or price changing while "won!" is still up is not a second sale', () => {
    const w = watcher()
    w.check(show(bidding('a', '$4', 'Lot #1')))
    w.check(show(won('a', '$5', 'Lot #1')))
    at(t + HOLD_MS)
    expect(w.check(show(won('a', '$5', 'Lot #1')))?.body).toBe(body('a', 'Lot #1', '5.00'))
    at(t + 500)
    expect(w.check(show(won('a', '$5', 'Lot #2')))).toBeNull()
    at(t + 5000)
    expect(w.check(show(won('a', '$5', 'Lot #2')))).toBeNull()
  })

  it('the next lot\'s title appearing under the old "won!" does not relabel the win', () => {
    const w = watcher()
    w.check(show(bidding('a', '$4', 'Lot #1')))
    w.check(show(won('a', '$5', 'Lot #1')))
    at(t + 800) // Whatnot puts up the next title before the hold is over
    expect(w.check(show(won('a', '$5', 'Lot #2')))).toBeNull()
    at(t + 400)
    expect(w.check(show(won('a', '$5', 'Lot #2')))?.body).toBe(body('a', 'Lot #1', '5.00'))
    // ...and when the next lot then runs, it is a win of its own.
    w.check(show(bidding('b', '$6', 'Lot #2')))
    w.check(show(won('b', '$7', 'Lot #2')))
    at(t + HOLD_MS)
    expect(w.check(show(won('b', '$7', 'Lot #2')))?.body).toBe(body('b', 'Lot #2', '7.00'))
  })

  it('the next auction is a new win, even for the same buyer', () => {
    const w = watcher()
    w.check(show(bidding('x', '$190', 'Lot #752')))
    w.check(show(won('chonwhite828', '$195', 'Lot #752')))
    at(t + HOLD_MS)
    const a = w.check(show(won('chonwhite828', '$195', 'Lot #752')))
    w.check(show(idle('Lot #753')))
    w.check(show(bidding('trickmon2', '$40', 'Lot #753')))
    w.check(show(won('chonwhite828', '$45', 'Lot #753')))
    at(t + HOLD_MS)
    const b = w.check(show(won('chonwhite828', '$45', 'Lot #753')))
    expect([a?.body, b?.body]).toEqual([body('chonwhite828', 'Lot #752', '195.00'), body('chonwhite828', 'Lot #753', '45.00')])
  })

  it('the same item, buyer and price again counts only after bidding in between, with a new id', () => {
    const w = watcher()
    const win = () => {
      w.check(show(won('lani808', '$10', 'Mystery box')))
      at(t + HOLD_MS)
      return w.check(show(won('lani808', '$10', 'Mystery box')))
    }
    w.check(show(bidding('lani808', '$9', 'Mystery box')))
    const first = win()
    w.check(show(idle('Mystery box')))
    expect(win()).toBeNull() // no auction ran in between
    w.check(show(bidding('lani808', '$9', 'Mystery box')))
    const second = win()
    expect(second?.body).toBe(first?.body)
    expect(second?.id).not.toBe(first?.id)
  })

  it('a "won!" already on screen when recording starts is never recorded (reload, Start fresh, Clear)', () => {
    const w = watcher()
    w.check(show(won('chonwhite828', '$195')))
    at(t + 10_000)
    expect(w.check(show(won('chonwhite828', '$195')))).toBeNull()
    w.check(show(idle('Next lot')))
    w.check(show(bidding('x_y', '$5', 'Next lot')))
    w.check(show(won('x_y', '$6', 'Next lot')))
    at(t + HOLD_MS)
    expect(w.check(show(won('x_y', '$6', 'Next lot')))?.body).toBe(body('x_y', 'Next lot', '6.00'))
  })

  it('a throttled tab that only checks when the player changes still records the win', () => {
    const w = watcher()
    w.check(show(bidding('ladyyy03', '$190')))
    w.check(show(won('chonwhite828', '$195'))) // the change to "won!"
    at(t + 4000) // nothing changed for 4 s; the next change is the next item
    const row = w.check(show(idle('Next lot')))
    expect(row?.body).toBe(body('chonwhite828', T760, '195.00'))
  })

  it('a resumed show numbers a later identical win after the saved one', () => {
    const before = watcher()
    before.check(show(bidding('a', '$9', 'Box')))
    before.check(show(won('a', '$10', 'Box')))
    at(t + HOLD_MS)
    const saved = before.check(show(won('a', '$10', 'Box')))!
    const after = watcher()
    after.remember([saved])
    after.check(show(bidding('a', '$9', 'Box')))
    after.check(show(won('a', '$10', 'Box')))
    at(t + HOLD_MS)
    const again = after.check(show(won('a', '$10', 'Box')))
    expect(again?.body).toBe(saved.body)
    expect(again?.id).not.toBe(saved.id)
  })

  it('the same win read twice in one show (a retry, a second tab) gets the same id', () => {
    const read = () => {
      const w = watcher()
      w.check(show(bidding('x', '$190')))
      w.check(show(won('chonwhite828', '$195')))
      at(t + HOLD_MS)
      return w.check(show(won('chonwhite828', '$195')))
    }
    expect(read()?.id).toBe(read()?.id)
  })
})

describe('a win on its way to the server', () => {
  it('passes the shared contract, and only the exact marker survives clamping', () => {
    const row = { id: '00000000000000a3', showId: SHOW, username: '', body: body('chon', 'Box', '195.00'), rawText: body('chon', 'Box', '195.00'), capturedAt: SHOW, seq: 3, source: 'player' as const }
    const batch = { version: 1, show: { id: SHOW, startedAt: SHOW }, rows: [clampRow(row)] }
    expect(validateBatch(batch)).toEqual({ ok: true })
    const chat = clampRow({ ...row, source: 'chat' as never })
    expect('source' in chat).toBe(false)
  })

  it('the body is exactly what the server parses', () => {
    expect(winBody({ winner: 'a.b-c_9', title: 'Red bundle x2 for $5', priceCents: 1205 })).toBe(
      'a.b-c_9 won the auction for Red bundle x2 for $5 for $12.05',
    )
  })
})
