/**
 * Card declines after a win. Whatnot charges the winner when the auction ends; sometimes the
 * card is declined a moment later and the buyer is asked to retry until the show ends. The
 * shop was told it shows on the show's Sold tab (buyer, item, price, status per row), maybe
 * in the player too. Neither has been captured from a real decline yet, so this reads any
 * platform UI on the page for a short decline phrase and ties it to a win recorded in this
 * show, or reports it untied for a person to sort out in Review.
 *
 * The chat is attacker-controlled: anyone watching can type "payment failed". The caller's
 * `ignore` keeps the chat (and the extension's panel) out entirely; on top of that only a
 * whole leaf counts, never a phrase inside a longer line, and inputs/editable text are skipped.
 *
 * DOM-only (no chrome.* calls), so it runs in jsdom like WinWatcher.
 */
import { winId } from './id'
import { HOLD_MS, type PlayerWin, declineBody, parseDeclineBody, parseWinBody, readPlayer } from './player'
import type { CapturedRow } from './types'

/**
 * A decline, as the whole text of one leaf element. UNVERIFIED: no real Whatnot decline has
 * been captured yet (player or Sold tab); these are the likely wordings. Whole-leaf match, so
 * a longer sentence never qualifies. Every wording has a space: a bare "Declined" is also a
 * valid username, and a viewer called that would decline their own win when it shows.
 */
export const DECLINE_PHRASE = /^(?:payment (?:failed|declined)|card declined|payment issue|failed payment)[.!]?$/i

export const UNTIED_PREFIX = 'payment declined (not tied to a win): '
/** Untied rows carry this much of the row the phrase was in, for the person in Review. */
export const CONTEXT_MAX = 200
/**
 * An untied decline waits longer than a tied one: right after "won!" the win is not recorded
 * yet (it has to hold HOLD_MS too), and a decline seen then becomes tied a moment later.
 */
export const UNTIED_HOLD_MS = 3000
/** How far up from the phrase its row may be. */
const MAX_LEVELS = 6
/** Untied declines per recording: past this, one viewer's variations cannot flood Review. */
export const MAX_UNTIED = 10
/** A container with more text than this is a page section, not a row: stop walking up. */
const MAX_SEGMENTS = 300
/** Longest text worth testing against DECLINE_PHRASE. */
const PHRASE_MAX = 40
const PLAYER = '[data-testid="live-player"]'
const STATUS = '[data-testid="show-winning-status"]'
const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'INPUT', 'TEXTAREA', 'SELECT', 'OPTION'])
const SHOW_ELEMENT = 1
const SHOW_TEXT = 4
const ACCEPT = 1
const REJECT = 2
const SKIP = 3

const norm = (s: string | null | undefined) => (s ?? '').replace(/\s+/g, ' ').trim()

/** The body of a decline row, tied or not (for counting them apart from wins). */
export const isDeclineBody = (body: string): boolean =>
  body.startsWith(UNTIED_PREFIX) || parseDeclineBody(body) !== null

/**
 * The controller's `ignore` for page readers: the extension's panel, the chat box the
 * Recorder watches, and anything shaped like a chat row near it (a copy the Recorder has not
 * found yet). Elsewhere a row-shaped element may be a Sold row; tie() trusts no chat anyway.
 */
export function outside(panel: (n: Node) => boolean, box: Element | null, rowSelectors: string[]): (n: Node) => boolean {
  const sels = [...new Set(rowSelectors.filter(Boolean))]
  return (n) => {
    if (panel(n) || (box !== null && (n === box || box.contains(n)))) return true
    if (n.nodeType !== 1) return false
    // Row-shaped elements are skipped only near the chat (a re-rendered copy next to the box):
    // page-wide, utility classes would also hide a real Sold-tab row.
    const up = box?.parentElement ?? null
    const near = up === null ? null : up === up.ownerDocument.body || up === up.ownerDocument.documentElement ? box : up
    if (near !== null && !near.contains(n)) return false
    for (const s of sels) {
      try {
        if ((n as Element).matches(s)) return true
      } catch {
        /* not a valid selector: ignore it */
      }
    }
    return false
  }
}

export interface DeclineScan {
  /** Look outside the player too (the Sold tab). Only when the chat box is known, so it can be skipped. */
  page: boolean
  /** Nodes never read: the chat and the extension's panel (see `outside`). */
  ignore?: (node: Node) => boolean
}

interface Win extends PlayerWin {
  id: string
  at: number
}

interface Pending {
  since: number
  scope: 'player' | 'page'
  win: Win | null
  context: string
}

/**
 * Turns decline phrases on the page into rows: one per recorded win at most, one per
 * distinct untied context at most, each after it held on screen.
 */
export class DeclineWatcher {
  private wins: Win[] = []
  private declined = new Set<string>()
  private sentContexts = new Set<string>()
  private seen = new Map<string, number>()
  private pending = new Map<string, Pending>()
  /** When the decline phrase now on the player footer first showed (null: none showing). */
  private footerSince: number | null = null

  constructor(
    readonly showId: string,
    private readonly now: () => number = Date.now,
  ) {}

  /** Rows already saved for this show: wins to tie to, declines not to send again. */
  remember(rows: Iterable<CapturedRow>): void {
    for (const r of rows) {
      if (r.source !== 'player') continue
      this.seen.set(r.body, (this.seen.get(r.body) ?? 0) + 1)
      const win = parseWinBody(r.body)
      if (win) {
        this.wins.push({ ...win, id: r.id, at: Date.parse(r.capturedAt) || 0 })
        continue
      }
      const d = parseDeclineBody(r.body)
      if (d) {
        const w = this.wins.find(
          (x) => !this.declined.has(x.id) && x.winner === d.winner && x.title === d.title && x.priceCents === d.priceCents,
        )
        if (w) this.declined.add(w.id)
      } else if (r.body.startsWith(UNTIED_PREFIX)) this.sentContexts.add(untiedKey(r.body.slice(UNTIED_PREFIX.length)))
    }
  }

  /** A win the WinWatcher just recorded: a decline can now be tied to it. */
  noteWin(row: CapturedRow): void {
    const win = row.source === 'player' ? parseWinBody(row.body) : null
    if (win) this.wins.push({ ...win, id: row.id, at: Date.parse(row.capturedAt) || this.now() })
  }

  /**
   * A decline phrase outside the player is still holding. The caller must keep scanning the
   * page while this is true, even if nothing changed: the hold is only met by a scan that
   * still sees the phrase.
   */
  get pageHolding(): boolean {
    for (const p of this.pending.values()) if (p.scope === 'page') return true
    return false
  }

  get declinedCount(): number {
    return this.declined.size
  }

  /** Rows for declines that just held long enough. `seq` is the caller's to set. */
  check(doc: Document, scan: DeclineScan): CapturedRow[] {
    const now = this.now()
    const ignore = scan.ignore ?? (() => false)
    const skip = (el: Element) =>
      SKIP_TAGS.has(el.tagName) ||
      el.hasAttribute('hidden') ||
      (el.hasAttribute('contenteditable') && el.getAttribute('contenteditable') !== 'false') ||
      ignore(el)
    const player = doc.querySelector(PLAYER)
    const root = scan.page ? doc.body : player
    const out: CapturedRow[] = []
    const found = new Set<string>()
    let inFooter = false
    if (root && !(root !== doc.body && skip(root))) {
      const leaves = declineLeaves(root, skip)
      inFooter = leaves.some((l) => !!player?.contains(l) && !!l.closest('footer')?.querySelector(STATUS))
      if (!inFooter) this.footerSince = null
      else if (this.footerSince === null) this.footerSince = now
      for (const leaf of leaves) {
        const inPlayer = !!player && player.contains(leaf)
        const { win, context } = this.tie(leaf, inPlayer, skip)
        if (win ? this.declined.has(win.id) : this.sentContexts.has(untiedKey(context)) || this.sentContexts.size >= MAX_UNTIED) continue
        const key = win ? 'win\u0000' + win.id : 'ctx\u0000' + untiedKey(context)
        if (found.has(key)) continue
        found.add(key)
        const p = this.pending.get(key)
        if (!p) {
          this.pending.set(key, { since: now, scope: inPlayer ? 'player' : 'page', win, context })
        } else if (now - p.since >= (win ? HOLD_MS : UNTIED_HOLD_MS)) {
          this.pending.delete(key)
          out.push(this.record(p))
        }
      }
    }
    for (const [key, p] of this.pending) {
      if (found.has(key) || (!scan.page && p.scope !== 'player')) continue
      this.pending.delete(key)
      // Gone now. The player is also checked on every change, so a tied decline there that had
      // held until this check counts (a throttled tab checks late), like a win does.
      if (p.win && p.scope === 'player' && now - p.since >= HOLD_MS && !this.declined.has(p.win.id)) out.push(this.record(p))
    }
    return out
  }

  /**
   * Which recorded win the decline at `leaf` is about, or the text to show a person.
   *
   * Only the player footer ties a decline to a win, and only while it still shows that very
   * win ("<winner> won!" with the title the win was recorded with) and the decline appeared
   * after it. Anything else, the Sold tab included until a real one has been captured, goes
   * untied with its row's text to Review: a viewer must never point a decline at a win.
   */
  private tie(leaf: Element, inPlayer: boolean, skip: (el: Element) => boolean): { win: Win | null; context: string } {
    const row = rowOf(leaf, skip)
    const context = norm((row?.segs ?? [norm(leaf.textContent)]).join(' ')).slice(0, CONTEXT_MAX).trim()
    if (inPlayer) {
      const footer = leaf.closest('footer')
      if (!footer?.querySelector(STATUS)) return { win: null, context }
      const now = readPlayer(leaf.ownerDocument)
      if (now.kind !== 'won') return { win: null, context }
      const shown = this.wins.filter((w) => w.winner === now.win.winner && w.title === now.win.title)
      const win = shown.length ? shown[shown.length - 1]! : null
      // A decline line already up before this win showed belongs to an earlier one.
      const since = this.footerSince ?? Number.POSITIVE_INFINITY
      return { win: win && since >= win.at ? win : null, context }
    }
    // Outside the player nothing is tied yet: no real Sold tab has been captured, and a chat
    // shown in another tab panel would look the same. The row goes to Review, where the server
    // lists the sale of the buyer it names first: one click for a person.
    return { win: null, context }
  }

  private record(p: Pending): CapturedRow {
    const body = p.win ? declineBody(p.win) : UNTIED_PREFIX + p.context
    if (p.win) this.declined.add(p.win.id)
    else this.sentContexts.add(untiedKey(p.context))
    const n = (this.seen.get(body) ?? 0) + 1
    this.seen.set(body, n)
    return {
      id: winId(this.showId, body, n),
      showId: this.showId,
      username: '',
      body,
      rawText: body,
      capturedAt: new Date(p.since).toISOString(),
      seq: 0,
      source: 'player',
    }
  }
}

/** The decline's row: the nearest ancestor with more text than the phrase (not a whole list). */
function rowOf(leaf: Element, skip: (el: Element) => boolean): { el: Element; segs: string[] } | null {
  const body = leaf.ownerDocument.body
  for (let a = leaf.parentElement, level = 0; a && a !== body && level < MAX_LEVELS; a = a.parentElement, level++) {
    const segs = segments(a, skip)
    if (!segs) return null // a page section, not a row
    if (segs.length >= 2) return { el: a, segs }
  }
  return null
}

/** One key for the same untied text however it is cased or punctuated. */
const untiedKey = (context: string): string => norm(context).toLowerCase().replace(/[.!]/g, '')

/** Leaf elements under `root` whose whole text is a decline phrase. Skipped subtrees are never entered. */
function declineLeaves(root: Element, skip: (el: Element) => boolean): Element[] {
  const out = new Set<Element>()
  const walker = root.ownerDocument.createTreeWalker(root, SHOW_ELEMENT | SHOW_TEXT, {
    acceptNode(n: Node) {
      if (n.nodeType === 1) return n !== root && skip(n as Element) ? REJECT : SKIP
      const t = n.textContent ?? ''
      return t.length <= PHRASE_MAX * 2 && t.trim() ? ACCEPT : SKIP
    },
  })
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const el = n.parentElement
    if (!el || out.has(el) || el.children.length) continue
    const t = norm(el.textContent)
    if (t.length <= PHRASE_MAX && DECLINE_PHRASE.test(t)) out.add(el)
  }
  return [...out]
}

/** Non-empty text segments under `el`, skipping ignored subtrees; null when it is too big to be a row. */
function segments(el: Element, skip: (el: Element) => boolean): string[] | null {
  const out: string[] = []
  const walker = el.ownerDocument.createTreeWalker(el, SHOW_ELEMENT | SHOW_TEXT, {
    acceptNode(n: Node) {
      if (n.nodeType === 1) return skip(n as Element) ? REJECT : SKIP
      return ACCEPT
    },
  })
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const t = norm(n.textContent)
    if (!t) continue
    out.push(t)
    if (out.length > MAX_SEGMENTS) return null
  }
  return out
}
