import { CONTEXT_MAX, DECLINE_PHRASE, DeclineWatcher, MAX_UNTIED, UNTIED_HOLD_MS, UNTIED_PREFIX, isDeclineBody, outside } from '../src/capture/decline'
import { winId } from '../src/capture/id'
import { HOLD_MS, WinWatcher, declineBody, parseDeclineBody, parseWinBody, winBody } from '../src/capture/player'
import type { CapturedRow } from '../src/capture/types'
import { clampRow } from '../src/upload/limits'
import { validateBatch } from '../src/upload/schema'
import { bidding, idle, won } from './fixtures/player'

// No real Whatnot decline has been captured yet (player or Sold tab): the markup below is made
// up to the shape the shop described (buyer, item, price, status per Sold-tab row).
const SHOW = '2026-10-24T21:00:00.000Z'
const T760 = '$1 PERFUMES/ COLOGNES/ NICHE WITH SAM #760'

const chat = (...lines: [string, string][]) =>
  `<div class="chat-scroll" id="chat"><div class="msg-list">${lines
    .map(([u, t]) => `<div class="msg-row"><span class="msg-user">${u}</span><span class="msg-text">${t}</span></div>`)
    .join('')}</div></div>`
const soldRow = (buyer: string, title: string, price: string, status: string) =>
  `<div class="row"><span>@${buyer}</span><span>${title}</span><span>${price}</span><span>${status}</span></div>`
const soldTab = (...rows: string[]) => `<div role="tabpanel">${rows.join('')}</div>`
/** A decline line added to the player footer, next to the "won!" status. */
const footerDecline = (html: string, text = 'Payment declined') =>
  html.replace('<footer class="LivePlayer_livePlayerFooter__UDpaa">', `$&<div><span>${text}</span></div>`)

let t = 0
const clock = () => t
const at = (ms: number) => {
  t = ms
}
const show = (...parts: string[]) => {
  document.body.innerHTML = parts.join('') + chat(['seed1', 'hello'], ['seed2', 'hi'], ['seed3', 'yo'])
  return document
}
/** What the controller passes: the chat box and its row shape, the way the Recorder knows them. */
const ignore = () => outside(() => false, document.getElementById('chat'), ['div.msg-row'])
const scan = (page = true) => ({ page, ignore: ignore() })

describe('DeclineWatcher', () => {
  let wins: WinWatcher
  let dw: DeclineWatcher
  beforeEach(() => {
    at(Date.parse('2026-10-24T22:00:00.000Z'))
    wins = new WinWatcher(SHOW, clock)
    dw = new DeclineWatcher(SHOW, clock)
  })

  /** Runs one auction to a recorded win and tells the decline watcher about it. */
  const recordWin = (name: string, price: string, title?: string): CapturedRow => {
    wins.check(show(bidding('someone', '$1', title)))
    wins.check(show(won(name, price, title)))
    at(t + HOLD_MS)
    const row = wins.check(show(won(name, price, title)))!
    expect(row).not.toBeNull()
    dw.noteWin(row)
    at(t + 100)
    return row
  }
  /** Checks the same page now and again after `wait`; returns every row produced. */
  const hold = (html: string[], wait = HOLD_MS, page = true): CapturedRow[] => {
    const out = dw.check(show(...html), scan(page))
    at(t + wait)
    return out.concat(dw.check(show(...html), scan(page)))
  }

  it('a decline in the player footer after a recorded win is one tied row with the win\'s own fields', () => {
    const win = recordWin('chonwhite828', '$188')
    const seenAt = t
    const page = [footerDecline(won('chonwhite828', '$188'))]
    expect(dw.check(show(...page), scan())).toEqual([]) // not held yet
    at(t + HOLD_MS)
    const rows = dw.check(show(...page), scan())
    const body = `chonwhite828 payment declined for ${T760} for $188.00`
    expect(rows).toEqual([
      {
        id: winId(SHOW, body, 1),
        showId: SHOW,
        username: '',
        body,
        rawText: body,
        capturedAt: new Date(seenAt).toISOString(),
        seq: 0,
        source: 'player',
      },
    ])
    expect(body).toBe(declineBody(parseWinBody(win.body)!))
    // Still on screen for a long time: still one row.
    for (let i = 0; i < 10; i++) {
      at(t + 1000)
      expect(dw.check(show(...page), scan())).toEqual([])
    }
  })

  it('the footer decline is read on the player-only (observer) check too', () => {
    recordWin('chonwhite828', '$188')
    const rows = hold([footerDecline(won('chonwhite828', '$188'))], HOLD_MS, false)
    expect(rows.map((r) => r.body)).toEqual([`chonwhite828 payment declined for ${T760} for $188.00`])
  })

  // Until a real Sold tab is captured, a decline there is never tied by the extension: a chat
  // shown in a tab panel would look the same. It goes untied, with the row's text, to Review.
  it('a decline on a Sold-tab row goes untied with the row\'s text', () => {
    recordWin('chonwhite828', '$188')
    const tab = soldTab(soldRow('trickmon2', 'Lot #1', '$5', 'Paid'), soldRow('chonwhite828', 'PERFUMES #760', '$188', 'Payment failed'))
    const rows = hold([idle('Next lot'), tab], UNTIED_HOLD_MS)
    expect(rows.map((r) => r.body)).toEqual([UNTIED_PREFIX + '@chonwhite828 PERFUMES #760 $188 Payment failed'])
  })

  it('a viewer line in a tab panel naming a winner, title and price is still only untied', () => {
    recordWin('victim', '$40', 'Big lot')
    const tab = soldTab('<div class="row"><span>mallory</span><a>@victim</a><span>Big lot</span><span>$40</span><span>payment failed</span></div>')
    const rows = hold([idle('x'), tab], UNTIED_HOLD_MS)
    expect(rows.every((r) => r.body.startsWith(UNTIED_PREFIX))).toBe(true)
  })

  it('a buyer with two wins and no title or price in the row: untied, never a guess', () => {
    recordWin('lani808', '$10', 'Mystery box A')
    recordWin('lani808', '$12', 'Mystery box B')
    const page = [idle('x'), soldTab('<div class="row"><span>lani808</span><span>Payment failed</span></div>')]
    expect(hold(page, UNTIED_HOLD_MS).map((r) => r.body)).toEqual([UNTIED_PREFIX + 'lani808 Payment failed'])
  })

  // ---- peer review (2026-10-27): who else can point a decline at a win ----

  it("a declined row whose buyer was not recorded never borrows another row's winner", () => {
    recordWin('alice', '$188', 'Perfume A')
    const page = [idle('x'), soldTab(soldRow('alice', 'Perfume A', '$188', 'Paid'), soldRow('bob', 'Lot B', '$20', 'Payment failed'))]
    const rows = hold(page, UNTIED_HOLD_MS)
    expect(rows.map((r) => r.body)).toEqual([UNTIED_PREFIX + '@bob Lot B $20 Payment failed'])
  })

  it('"@name" inside other text does not tie: only a whole segment names the buyer', () => {
    recordWin('alice', '$188', 'Perfume A')
    const page = [idle('x'), soldTab('<div class="row"><span>shoutout @alice</span><span>Payment failed</span></div>')]
    expect(hold(page, UNTIED_HOLD_MS).every((r) => r.body.startsWith(UNTIED_PREFIX))).toBe(true)
  })

  it('outside the player, only a tab panel (the Sold tab) can tie a decline', () => {
    recordWin('alice', '$188', 'Perfume A')
    const page = [idle('x'), '<section>' + soldRow('alice', 'Perfume A', '$188', 'Payment failed') + '</section>']
    expect(hold(page, UNTIED_HOLD_MS).every((r) => r.body.startsWith(UNTIED_PREFIX))).toBe(true)
  })

  it("a chat copy the recorder missed cannot decline a winner who chatted in it", () => {
    recordWin('chonwhite828', '$188')
    const overlay = '<div class="chat-v2"><div class="row-v2"><span>chonwhite828</span><span>thanks!</span></div><div class="row-v2"><span>troll</span><span>Card declined</span></div></div>'
    const rows = hold([idle('x'), overlay], UNTIED_HOLD_MS)
    expect(rows.some((r) => !r.body.startsWith(UNTIED_PREFIX))).toBe(false)
  })

  it("a decline still on the footer when the next lot is won is not the next winner's", () => {
    recordWin('alice', '$188', 'Perfume A')
    recordWin('bob', '$50', 'Lot B')
    const rows = hold([footerDecline(won('bob', '$50', 'Lot C'))])
    expect(rows.some((r) => r.body.startsWith('bob payment declined'))).toBe(false)
  })

  it('case and punctuation variations of one untied text are one row, and untied rows are capped', () => {
    const variants = ['Payment failed', 'PAYMENT FAILED', 'Payment Failed!', 'payment failed.']
    for (const v of variants) hold([idle('x'), soldTab(`<div class="row"><span>Order 1</span><span>${v}</span></div>`)], UNTIED_HOLD_MS)
    let n = 0
    for (let i = 0; i < 30; i++) n += hold([idle('x'), soldTab(`<div class="row"><span>Order ${i + 100}</span><span>Card declined</span></div>`)], UNTIED_HOLD_MS).length
    expect(n).toBe(MAX_UNTIED - 1)
  })

  it('a decline with no recognisable winner is one untied row with the row\'s text, never repeated', () => {
    recordWin('chonwhite828', '$188')
    const page = [idle('x'), soldTab('<div class="row"><span>Order 1234</span><span>Card declined</span></div>')]
    expect(hold(page, HOLD_MS)).toEqual([]) // an untied decline waits longer
    at(t + UNTIED_HOLD_MS)
    const rows = dw.check(show(...page), scan())
    const body = UNTIED_PREFIX + 'Order 1234 Card declined'
    expect(rows).toEqual([expect.objectContaining({ body, rawText: body, username: '', source: 'player', id: winId(SHOW, body, 1) })])
    for (let i = 0; i < 5; i++) {
      at(t + UNTIED_HOLD_MS)
      expect(dw.check(show(...page), scan())).toEqual([])
    }
    // A resumed recording knows it was sent.
    const again = new DeclineWatcher(SHOW, clock)
    again.remember(rows)
    again.check(show(...page), scan())
    at(t + UNTIED_HOLD_MS)
    expect(again.check(show(...page), scan())).toEqual([])
  })

  it('an untied context is cut to 200 characters', () => {
    const long = 'x'.repeat(500)
    const page = [soldTab(`<div class="row"><span>${long}</span><span>Payment declined</span></div>`)]
    hold(page, 0)
    at(t + UNTIED_HOLD_MS)
    const [row] = dw.check(show(...page), scan())
    expect(row!.body).toBe(UNTIED_PREFIX + 'x'.repeat(CONTEXT_MAX))
  })

  it('a decline for a buyer never recorded in this recording is untied', () => {
    recordWin('chonwhite828', '$188')
    const page = [idle('x'), soldTab(soldRow('stranger99', 'Lot #4', '$20', 'Payment failed'))]
    hold(page, 0)
    at(t + UNTIED_HOLD_MS)
    expect(dw.check(show(...page), scan()).map((r) => r.body)).toEqual([UNTIED_PREFIX + '@stranger99 Lot #4 $20 Payment failed'])
  })

  it('several recorded buyers in the same container is untied', () => {
    recordWin('aaa', '$1', 'A')
    recordWin('bbb', '$2', 'B')
    const page = [soldTab('<div><span>aaa</span><span>bbb</span><span>Payment failed</span></div>')]
    hold(page, 0)
    at(t + UNTIED_HOLD_MS)
    expect(dw.check(show(...page), scan()).map((r) => r.body)).toEqual([UNTIED_PREFIX + 'aaa bbb Payment failed'])
  })

  it('a viewer typing a decline in the chat never produces a row', () => {
    recordWin('chonwhite828', '$188')
    const lines = chat(
      ['viewer1', 'payment declined'],
      ['chonwhite828', 'payment failed'],
      ['chonwhite828', 'Payment failed'],
      ['troll', 'Declined'],
      ['troll', '@chonwhite828'],
    )
    // The chat box is known, and also when only its row shape is (a copy the Recorder has not found).
    const checks = [
      () => outside(() => false, document.getElementById('chat2'), []),
      () => outside(() => false, null, ['div.msg-row']),
    ]
    for (const ig of checks) {
      document.body.innerHTML = idle('x') + lines.replace('id="chat"', 'id="chat2"')
      for (let i = 0; i < 6; i++) {
        expect(dw.check(document, { page: true, ignore: ig() })).toEqual([])
        at(t + UNTIED_HOLD_MS)
      }
    }
  })

  it('a chat overlay inside the player is skipped too', () => {
    recordWin('chonwhite828', '$188')
    const overlay = chat(['chonwhite828', 'Payment failed'])
    const html = won('chonwhite828', '$188').replace('<section class="LivePlayer_livePlayer__WDoms">', `$&${overlay}`)
    document.body.innerHTML = html
    const ig = outside(() => false, null, ['div.msg-row'])
    for (let i = 0; i < 4; i++) {
      expect(dw.check(document, { page: false, ignore: ig })).toEqual([])
      expect(dw.check(document, { page: true, ignore: ig })).toEqual([])
      at(t + UNTIED_HOLD_MS)
    }
  })

  it('a decline phrase inside a longer line, an input or editable text is not a decline', () => {
    recordWin('chonwhite828', '$188')
    const page = [
      idle('x'),
      '<div class="note"><span>chonwhite828</span><span>my payment declined lol</span></div>',
      '<div><span>chonwhite828</span><input value="Payment failed"><textarea>Payment failed</textarea></div>',
      '<div><span>chonwhite828</span><div contenteditable="true">Payment failed</div></div>',
      '<div><span>chonwhite828</span><b>Payment <i>failed</i></b></div>',
    ]
    for (let i = 0; i < 4; i++) {
      expect(dw.check(show(...page), { page: true })).toEqual([])
      at(t + UNTIED_HOLD_MS)
    }
  })

  it('the extension\'s own panel is never read', () => {
    recordWin('chonwhite828', '$188')
    const page = [idle('x'), '<div id="panel"><span>chonwhite828</span><span>Payment failed</span></div>']
    const ig = (n: Node) => {
      const p = document.getElementById('panel')
      return !!p && (n === p || p.contains(n))
    }
    for (let i = 0; i < 4; i++) {
      expect(dw.check(show(...page), { page: true, ignore: outside(ig, document.getElementById('chat'), []) })).toEqual([])
      at(t + UNTIED_HOLD_MS)
    }
  })

  it('a decline has to hold: a flicker shorter than HOLD_MS is nothing', () => {
    recordWin('chonwhite828', '$188')
    const on = [footerDecline(won('chonwhite828', '$188'))]
    const off = [won('chonwhite828', '$188')]
    expect(dw.check(show(...on), scan())).toEqual([])
    at(t + HOLD_MS - 300)
    expect(dw.check(show(...off), scan())).toEqual([]) // gone before it held
    expect(dw.check(show(...on), scan())).toEqual([]) // back: the hold starts again
    at(t + HOLD_MS - 1)
    expect(dw.check(show(...on), scan())).toEqual([])
    at(t + 1)
    expect(dw.check(show(...on), scan())).toHaveLength(1)
  })

  it('a decline seen twice (and again later) is one row per win', () => {
    recordWin('chonwhite828', '$188')
    const page = [footerDecline(won('chonwhite828', '$188'))]
    expect(hold(page)).toHaveLength(1)
    at(t + 60_000)
    expect(hold(page)).toEqual([])
    // Gone and back: still the same win, still declined.
    expect(hold([idle('x')])).toEqual([])
    expect(hold(page)).toEqual([])
  })

  it('a player-only check does not cancel a Sold-tab decline that is still holding (untied)', () => {
    recordWin('chonwhite828', '$188')
    const page = [idle('x'), soldTab(soldRow('chonwhite828', T760, '$188', 'Payment failed'))]
    expect(dw.check(show(...page), scan(true))).toEqual([])
    at(t + 400)
    expect(dw.check(show(...page), scan(false))).toEqual([])
    at(t + UNTIED_HOLD_MS)
    expect(dw.check(show(...page), scan(true))).toHaveLength(1)
  })

  it('a resumed recording ties to saved wins and does not decline a saved decline again', () => {
    const win = recordWin('chonwhite828', '$188')
    const page = [footerDecline(won('chonwhite828', '$188'))]
    const [decl] = hold(page)
    expect(decl).toBeDefined()
    const resumed = new DeclineWatcher(SHOW, clock)
    resumed.remember([win, decl!])
    expect(resumed.declinedCount).toBe(1)
    resumed.check(show(...page), scan())
    at(t + HOLD_MS * 5)
    expect(resumed.check(show(...page), scan())).toEqual([])
    // Seeded from the saved win alone, the same decline ties to it.
    const fresh = new DeclineWatcher(SHOW, clock)
    fresh.remember([win])
    fresh.check(show(...page), scan())
    at(t + HOLD_MS)
    const [row] = fresh.check(show(...page), scan())
    expect(row?.body).toBe(decl!.body)
    expect(row?.id).toBe(decl!.id) // a replayed decline is the same row
  })

  it('a decline without any recorded win is untied, never tied to the buyer on screen', () => {
    // "won!" was already up when recording started: that win was never recorded.
    const page = [footerDecline(won('chonwhite828', '$188'))]
    hold(page, 0)
    at(t + UNTIED_HOLD_MS)
    const rows = dw.check(show(...page), scan())
    expect(rows).toHaveLength(1)
    expect(rows[0]!.body.startsWith(UNTIED_PREFIX)).toBe(true)
  })
})

describe('decline rows', () => {
  it('the phrase is a whole leaf, in a few wordings', () => {
    for (const s of ['Payment failed', 'payment declined', 'CARD DECLINED.', 'Payment issue', 'Failed payment!'])
      expect(DECLINE_PHRASE.test(s)).toBe(true)
    // "Declined" alone is also a valid username: never a decline.
    for (const s of ['my payment declined lol', 'chonwhite828 payment failed', 'Payment', 'Declined', 'declined?', 'not declined'])
      expect(DECLINE_PHRASE.test(s)).toBe(false)
  })

  it('bodies read back, and are told apart from wins', () => {
    const w = { winner: 'a.b-c_9', title: 'Red bundle x2 for $5', priceCents: 1205 }
    expect(declineBody(w)).toBe('a.b-c_9 payment declined for Red bundle x2 for $5 for $12.05')
    expect(parseDeclineBody(declineBody(w))).toEqual(w)
    expect(parseWinBody(winBody(w))).toEqual(w)
    expect(parseWinBody(declineBody(w))).toBeNull()
    expect(isDeclineBody(declineBody(w))).toBe(true)
    expect(isDeclineBody(UNTIED_PREFIX + 'x')).toBe(true)
    expect(isDeclineBody(winBody(w))).toBe(false)
  })

  it('pass the shared contract as player rows', () => {
    const body = declineBody({ winner: 'chon', title: 'Box', priceCents: 19500 })
    const rows = [body, UNTIED_PREFIX + 'Order 1 Declined'].map((b, i) =>
      clampRow({ id: `00000000000000a${i}`, showId: SHOW, username: '', body: b, rawText: b, capturedAt: SHOW, seq: i + 1, source: 'player' }),
    )
    expect(rows.every((r) => r.source === 'player')).toBe(true)
    expect(validateBatch({ version: 1, show: { id: SHOW, startedAt: SHOW }, rows })).toEqual({ ok: true })
  })
})

describe('a buyer named like a decline', () => {
  it('winning as "declined" does not decline their own win', () => {
    const doc = new DOMParser().parseFromString(
      `<body><section data-testid="live-player"><footer><div><strong data-testid="show-winning-status">declined <strong>won!</strong></strong></div><span data-testid="show-product-title">Red bundle</span><section><strong>$25</strong><strong>Sold</strong></section></footer></section></body>`,
      'text/html',
    )
    let now = 0
    const dw = new DeclineWatcher('S', () => now)
    dw.noteWin({ id: 'w1', showId: 'S', username: '', body: 'declined won the auction for Red bundle for $25.00', rawText: '', capturedAt: new Date(0).toISOString(), seq: 1, source: 'player' })
    expect(dw.check(doc, { page: true })).toEqual([])
    now = 10_000
    expect(dw.check(doc, { page: true })).toEqual([])
  })
})

describe('the chat-row exclusion', () => {
  it('does not hide a Sold row that shares the chat rows\' utility classes', () => {
    document.body.innerHTML =
      '<main><div><div id="chat"><div class="flex gap-2"><span>seed</span><span>hi</span></div></div></div></main>' +
      '<aside><div role="tabpanel"><div class="flex gap-2 items-center"><span>@alice</span><span>Payment failed</span></div></div></aside>'
    const ignore = outside(() => false, document.getElementById('chat'), ['div.flex.gap-2'])
    const sold = document.querySelector('.items-center')!
    const copy = document.querySelector('#chat .flex')!
    expect(ignore(sold)).toBe(false)
    expect(ignore(copy)).toBe(true)
  })
})

describe('a footer decline line that stays up', () => {
  it('is not the next winner\'s decline', () => {
    let now = Date.parse('2026-10-24T22:00:00.000Z')
    const clock = () => now
    const wins = new WinWatcher(SHOW, clock)
    const dw = new DeclineWatcher(SHOW, clock)
    const record = (name: string, price: string, title: string) => {
      wins.check(show(bidding('x', '$1', title)))
      wins.check(show(won(name, price, title)))
      now += HOLD_MS
      dw.noteWin(wins.check(show(won(name, price, title)))!)
      now += 100
    }
    const withDecline = (h: string) => h.replace('<footer class="LivePlayer_livePlayerFooter__UDpaa">', '$&<div><span>Payment failed</span></div>')
    record('alice', '$188', 'Lot A')
    const first = [...dw.check(show(withDecline(won('alice', '$188', 'Lot A'))), scan(false))]
    now += HOLD_MS
    first.push(...dw.check(show(withDecline(won('alice', '$188', 'Lot A'))), scan(false)))
    expect(first.map((r) => r.body)).toEqual(['alice payment declined for Lot A for $188.00'])
    record('bob', '$50', 'Lot B') // the decline line never went away
    const next: CapturedRow[] = []
    for (let i = 0; i < 3; i++) {
      next.push(...dw.check(show(withDecline(won('bob', '$50', 'Lot B'))), scan(false)))
      now += UNTIED_HOLD_MS
    }
    expect(next.some((r) => r.body.startsWith('bob payment declined'))).toBe(false)
  })
})
