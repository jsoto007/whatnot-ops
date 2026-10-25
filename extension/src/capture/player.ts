/**
 * Auction results from Whatnot's video player. Whatnot never puts the win in the chat;
 * the player footer shows it (read from a live show, 2026-10-24):
 *
 *   bidding  [data-testid=show-winning-status] "mhassell is" + "Winning!"   price "$140" + timer "00:09"
 *   won      [data-testid=show-winning-status] "chonwhite828" + "won!"      price "$188" + "Sold"
 *   item     [data-testid=show-product-title]  "$1 PERFUMES/ COLOGNES/ NICHE WITH SAM #760"
 *
 * The won footer also carries a "bid $183.00" line by the winner's avatar: someone's last
 * bid, not the price. The price is only ever the element right before "Sold", inside the
 * same footer as the status line, which viewers cannot write into.
 *
 * DOM-only (no chrome.* calls), so it runs in jsdom like the chat recorder.
 */
import { winId } from './id'
import { leafSegments } from './text'
import type { CapturedRow } from './types'

export interface PlayerWin {
  winner: string
  title: string
  priceCents: number
}

export type PlayerState = { kind: 'won'; win: PlayerWin } | { kind: 'bidding' } | { kind: 'none' }

const PRICE = /^\$(\d{1,3}(?:,\d{3})+|\d{1,6})(?:\.(\d{2}))?$/
/** Same as the server's PLAYER_WIN buyer (api/app/services/rules.py). */
const WINNER = /^[A-Za-z0-9._-]{1,80}$/
/** $100,000: the payment-file import treats anything larger as a misread too. */
export const MAX_CENTS = 10_000_000
/** The server keeps item text to 240 characters; cutting here keeps the price on the line. */
export const TITLE_MAX = 240
/** "won!" has to stay put this long: a first frame with a stale price is not the sale. */
export const HOLD_MS = 1000

/** "$188" / "$1,250" / "$19.50" → integer cents; anything else (including "bid $183.00") → null. */
export function priceCents(text: string): number | null {
  const m = PRICE.exec(text.trim())
  if (!m) return null
  const cents = Number(m[1]!.replace(/,/g, '')) * 100 + Number(m[2] ?? 0)
  return cents <= MAX_CENTS ? cents : null
}

export const dollars = (cents: number): string => `${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, '0')}`

/** The one line a win is stored and sent as; the server reads it back only from player rows. */
export const winBody = (w: PlayerWin): string => `${w.winner} won the auction for ${w.title} for $${dollars(w.priceCents)}`

const leaves = (root: Element, text: string) =>
  [...root.querySelectorAll('*')].filter((e) => e.children.length === 0 && e.textContent?.trim() === text)

export function readPlayer(doc: Document): PlayerState {
  const player = doc.querySelector('[data-testid="live-player"]')
  const status = player?.querySelector('[data-testid="show-winning-status"]')
  if (!player || !status) return { kind: 'none' }
  const seg = leafSegments(status)
  const last = (seg[seg.length - 1] ?? '').toLowerCase()
  if (last === 'winning!') return { kind: 'bidding' }
  if (seg.length !== 2 || last !== 'won!' || !WINNER.test(seg[0]!)) return { kind: 'none' }
  const footer = status.closest('footer')
  const titleEl = footer?.querySelector('[data-testid="show-product-title"]')
  const title = (titleEl?.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, TITLE_MAX).trim()
  const sold = footer ? leaves(footer, 'Sold') : []
  // Exactly one "Sold", with the price right before it; anything else is not read as a sale.
  if (!title || sold.length !== 1) return { kind: 'none' }
  const cents = priceCents(sold[0]!.previousElementSibling?.textContent ?? '')
  if (cents === null) return { kind: 'none' }
  return { kind: 'won', win: { winner: seg[0]!, title, priceCents: cents } }
}

/**
 * Turns successive player readings into one row per win.
 *
 * - A win counts only when bidding ("is Winning!") was seen since the last one, during this
 *   recording: a "won!" already on screen when recording starts (a reload, Start fresh,
 *   Clear) belongs to an auction that ended before, and is never recorded again.
 * - The reading has to hold for HOLD_MS. Checks run on every change to the player and on a
 *   timer, so the time until the reading changes is how long it held.
 * - One "won!" display gives at most one win: if the item or price changes while it is
 *   still up, the change is not a second sale.
 * - The item is the one showing when "won!" first appeared: Whatnot can put up the next
 *   lot's title while the old "won!" is still there. The price is the reading that held
 *   (a first frame can carry a stale price).
 */
export class WinWatcher {
  private seen = new Map<string, number>()
  private armed = false
  private shown: { body: string; since: number } | null = null
  private recordedThisDisplay = false
  /** Winner and item from the first "won!" reading of the current display. */
  private display: { winner: string; title: string } | null = null

  constructor(
    readonly showId: string,
    private readonly now: () => number = Date.now,
  ) {}

  /** Wins already saved for this show, so a genuine later repeat gets a new id. */
  remember(rows: Iterable<CapturedRow>): void {
    for (const r of rows) if (r.source === 'player') this.seen.set(r.body, (this.seen.get(r.body) ?? 0) + 1)
  }

  /** A row for a win that just held long enough, or null. `seq` is the caller's to set. */
  check(doc: Document): CapturedRow | null {
    const state = readPlayer(doc)
    const now = this.now()
    if (state.kind !== 'won') this.display = null
    else if (this.display?.winner !== state.win.winner) this.display = { winner: state.win.winner, title: state.win.title }
    const body = state.kind === 'won' ? winBody({ ...state.win, title: this.display!.title }) : null
    let row: CapturedRow | null = null
    if (this.shown && body !== this.shown.body) {
      // The reading changed: it held from `since` until now.
      if (now - this.shown.since >= HOLD_MS) row = this.record(this.shown.body, this.shown.since)
      this.shown = null
    }
    if (body === null) {
      this.recordedThisDisplay = false
      if (state.kind === 'bidding') this.armed = true
      return row
    }
    if (!this.shown) this.shown = { body, since: now }
    else if (!row && now - this.shown.since >= HOLD_MS) row = this.record(body, this.shown.since)
    return row
  }

  /** `wonAt` is when "won!" first showed, not when the hold check passed: it is the sale's time. */
  private record(body: string, wonAt: number): CapturedRow | null {
    if (this.recordedThisDisplay || !this.armed) return null
    this.recordedThisDisplay = true
    this.armed = false
    const n = (this.seen.get(body) ?? 0) + 1
    this.seen.set(body, n)
    return {
      id: winId(this.showId, body, n),
      showId: this.showId,
      username: '',
      body,
      rawText: body,
      capturedAt: new Date(wonAt).toISOString(),
      seq: 0,
      source: 'player',
    }
  }
}
