import { Deduper } from './dedup'
import { rowId } from './id'
import { autoDetectRows, chatContainer } from './row'
import { leafSegments, rowText, splitRow } from './text'
import type { CapturedRow } from './types'

export type RecorderEvent =
  | { type: 'row'; row: CapturedRow }
  | { type: 'healed'; selector: string }
  /** The chat is not on screen. Recording carries on and `healed` follows once it is back. */
  | { type: 'lost'; reason: string }

export interface RecorderOptions {
  /** Nodes to ignore entirely (our own panel). */
  ignore?: (node: Node) => boolean
  now?: () => number
  /** How often the watchdog looks for a dead selector / detached container. */
  watchdogMs?: number
  /** No matched row for this long while the DOM kept changing = selector is stale. */
  staleMs?: number
  /** Mutations without a matched row before the selector counts as stale. */
  staleMutations?: number
}

/**
 * Watches one chat container for rows matching a selector and emits
 * structured CapturedRows. DOM-only: no chrome.* APIs, so it runs in jsdom.
 *
 * Self-healing: a watchdog notices when (a) the container left the document,
 * or (b) the DOM keeps changing but nothing matches the selector any more.
 * It follows the rows to wherever the chat is now, re-detects the row shape
 * inside the same container, or waits (`lost`) until the chat is back. It
 * never gives up while running, and never guesses from the whole page: that
 * is how a recorder ends up watching the product list instead of the chat.
 */
export class Recorder {
  private observer: MutationObserver | null = null
  private doc: Document | null = null
  private container: Element | null = null
  /** Chat not on screen; `lost` was reported once and `healed` comes when it is back. */
  private waiting = false
  private selector = ''
  /** Selectors this chat used before (a reload may bring the older row shape back). */
  private fallbacks: string[] = []
  private dedup: Deduper
  private seq = 0
  private lastRowAt = 0
  private mutationsSinceRow = 0
  private watchdog: ReturnType<typeof setInterval> | null = null
  private listeners = new Set<(e: RecorderEvent) => void>()
  private readonly now: () => number
  private readonly opts: Required<Omit<RecorderOptions, 'ignore' | 'now'>>
  private readonly ignore: (node: Node) => boolean

  constructor(
    readonly showId: string,
    opts: RecorderOptions = {},
  ) {
    this.now = opts.now ?? Date.now
    this.ignore = opts.ignore ?? (() => false)
    this.opts = {
      watchdogMs: opts.watchdogMs ?? 3000,
      staleMs: opts.staleMs ?? 8000,
      staleMutations: opts.staleMutations ?? 5,
    }
    this.dedup = new Deduper()
  }

  on(fn: (e: RecorderEvent) => void): () => void {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  get rowCount(): number {
    return this.seq
  }

  get currentSelector(): string {
    return this.selector
  }

  /** The chat box being watched (the last one, while waiting). Other page readers must stay out of it. */
  get box(): Element | null {
    return this.container
  }

  get running(): boolean {
    return this.watchdog !== null
  }

  /** Running and watching chat rows on screen (not waiting for the chat to come back). */
  get attached(): boolean {
    return this.running && !this.waiting
  }

  /**
   * Start on `container` with `selector`. `seq` continues from `startSeq`
   * (a resumed show) and `knownTexts` are skipped during the initial scan.
   */
  start(container: Element, selector: string, startSeq = 0, knownTexts: Iterable<string> = []): void {
    this.begin(container.ownerDocument, selector, startSeq, knownTexts)
    this.attach(container)
  }

  /**
   * Start without a click (a reloaded page): find rows matching one of
   * `selectors` (newest first) now, or as soon as the chat renders. Until then
   * it waits (`lost`).
   */
  resume(doc: Document, selectors: string[], startSeq = 0, knownTexts: Iterable<string> = []): void {
    this.begin(doc, selectors[0] ?? '', startSeq, knownTexts)
    this.fallbacks = selectors.slice(1)
    if (!this.locate()) this.wait('waiting for the chat to load')
  }

  stop(): void {
    this.observer?.disconnect()
    this.observer = null
    if (this.watchdog) clearInterval(this.watchdog)
    this.watchdog = null
  }

  private begin(doc: Document, selector: string, startSeq: number, knownTexts: Iterable<string>): void {
    this.stop()
    this.doc = doc
    this.container = null
    this.selector = selector
    this.fallbacks = []
    this.seq = startSeq
    this.waiting = false
    this.dedup = new Deduper()
    this.dedup.setKnownTail(knownTexts)
    this.watchdog = setInterval(() => this.check(), this.opts.watchdogMs)
  }

  /** Exposed for tests; runs on the watchdog interval. */
  check(): void {
    if (!this.running || !this.doc) return
    if (!this.container || !this.doc.contains(this.container)) {
      if (!this.locate()) this.wait('the chat left the page')
      return
    }
    if (this.waiting) {
      // Rows came back into the same container (the observer already recorded them), or appeared elsewhere.
      if (this.hasRows(this.container)) this.recovered()
      else this.locate()
      return
    }
    const stale =
      this.mutationsSinceRow >= this.opts.staleMutations && this.now() - this.lastRowAt >= this.opts.staleMs
    if (stale) this.refresh()
  }

  /** The chat kept changing but no new row matched: new class names, a quiet chat, or no chat at all. */
  private refresh(): void {
    const container = this.container!
    const found = autoDetectRows(container)
    if (found && found.selector !== this.selector) {
      // Same chat, new row shape (Whatnot re-rendered with new classes).
      this.fallbacks = [this.selector, ...this.fallbacks.filter((s) => s !== found.selector)]
      this.selector = found.selector
      this.attach(container)
      this.emit({ type: 'healed', selector: found.selector })
      return
    }
    if (found || this.hasRows(container)) {
      // The rows are still there: a quiet chat with other things moving (reactions, banners). Not lost.
      this.mutationsSinceRow = 0
      this.lastRowAt = this.now()
      return
    }
    if (!this.locate()) this.wait('no chat messages on screen')
  }

  /** Rows matching a known selector anywhere on the page (Whatnot re-mounted the chat): watch their box. */
  private locate(): boolean {
    for (const selector of [this.selector, ...this.fallbacks]) {
      const row = this.findRow(selector)
      if (!row) continue
      if (selector !== this.selector) {
        this.fallbacks = [this.selector, ...this.fallbacks.filter((s) => s !== selector)]
        this.selector = selector
      }
      this.attach(chatContainer(row))
      this.emit({ type: 'healed', selector })
      return true
    }
    return false
  }

  private findRow(selector: string): Element | null {
    if (!selector) return null
    const old = this.container
    let first: Element | null = null
    for (const el of this.doc!.querySelectorAll(selector)) {
      if (this.ignore(el) || (old && old.contains(el))) continue
      first ??= el
      // A layout may keep a hidden copy of the chat; prefer the one on screen.
      if (el.getClientRects().length) return el
    }
    return first
  }

  private hasRows(container: Element): boolean {
    for (const el of container.querySelectorAll(this.selector)) if (!this.ignore(el)) return true
    return false
  }

  private wait(reason: string): void {
    if (this.waiting) return
    this.waiting = true
    this.emit({ type: 'lost', reason })
  }

  private recovered(): void {
    this.waiting = false
    this.mutationsSinceRow = 0
    this.lastRowAt = this.now()
    this.emit({ type: 'healed', selector: this.selector })
  }

  /** Watch `container`; rows already on screen that were recorded (or saved) before are not repeated. */
  private attach(container: Element): void {
    this.observer?.disconnect()
    this.container = container
    this.waiting = false
    this.mutationsSinceRow = 0
    this.lastRowAt = this.now()
    this.scan(container, true)
    this.observe(container)
  }

  private observe(container: Element): void {
    this.observer = new MutationObserver((muts) => this.onMutations(muts))
    this.observer.observe(container, { childList: true, subtree: true })
  }

  /** Exposed for tests. One batch = one framework render; replayed rows are skipped in order. */
  onMutations(muts: MutationRecord[]): void {
    const added: Element[] = []
    for (const m of muts) {
      for (const n of m.addedNodes) {
        if (n.nodeType === 1 && !this.ignore(n)) added.push(n as Element)
      }
    }
    if (!added.length || !this.selector) return
    this.mutationsSinceRow += added.length
    const fresh: Element[] = []
    for (const node of added) {
      for (const el of this.matching(node)) {
        if (!this.dedup.seenElement(el)) fresh.push(el)
      }
    }
    const texts = fresh.map(rowText)
    const replay = this.dedup.replayPrefix(texts)
    fresh.forEach((el, i) => {
      if (i >= replay) this.recordText(el, texts[i]!, false)
    })
  }

  private matching(node: Element): Element[] {
    const out: Element[] = []
    if (node.matches(this.selector)) out.push(node)
    for (const el of node.querySelectorAll(this.selector)) if (!this.ignore(el)) out.push(el)
    return out
  }

  private scan(node: Element, initial: boolean): void {
    if (!this.selector || this.ignore(node)) return
    for (const el of this.matching(node)) this.record(el, initial)
  }

  private record(el: Element, initial: boolean): void {
    if (this.dedup.seenElement(el)) return
    this.recordText(el, rowText(el), initial)
  }

  private recordText(el: Element, rawText: string, initial: boolean): void {
    if (!rawText) return
    const now = this.now()
    if (this.dedup.isDuplicate(rawText, now, initial)) return
    this.dedup.remember(rawText, now)
    const { username, body } = splitRow(leafSegments(el), rawText)
    const row: CapturedRow = {
      id: rowId(this.showId, username, body, now),
      showId: this.showId,
      username,
      body,
      rawText,
      capturedAt: new Date(now).toISOString(),
      seq: ++this.seq,
    }
    this.lastRowAt = now
    this.mutationsSinceRow = 0
    this.emit({ type: 'row', row })
  }

  private emit(e: RecorderEvent): void {
    for (const fn of this.listeners) fn(e)
  }
}
